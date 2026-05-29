"""
LLM 优选模块
==============
对四层漏斗 + E 层输出的候选做 LLM 二次优选：
  1. 汇总候选标的的多维信号（B/C/E 层因子 + 基本面 + 行业）
  2. 构造结构化 prompt 发送给 LLM
  3. LLM 返回优选排序 + 置信度 + 理由
  4. 合并量化分和 LLM 分为最终排序

设计原则：
  - LLM 是"辅助优选"而非"替代量化"
  - 量化分权重 60%，LLM 分权重 40%
  - LLM 不可用时自动降级为纯量化排序
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional

import numpy as np

from .config import MainUptrendConfig
from .data_loader import DataLoader

logger = logging.getLogger(__name__)


class LLMRefiner:
    """LLM 优选器"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
            api_key = os.environ.get("OPENAI_API_KEY", "")
            base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            if not api_key:
                logger.warning("OPENAI_API_KEY 未设置，LLM 优选不可用")
                return None
            self._client = OpenAI(api_key=api_key, base_url=base_url)
            return self._client
        except ImportError:
            logger.warning("openai 库未安装，LLM 优选不可用")
            return None
        except Exception as e:
            logger.warning(f"LLM 客户端初始化失败: {e}")
            return None

    def refine(self, candidates: List[Dict], eval_date: str) -> List[Dict]:
        if not candidates:
            return candidates

        if not self.cfg.llm_enabled:
            return candidates

        client = self._get_client()
        if client is None:
            logger.info("LLM 不可用，使用纯量化排序")
            return candidates

        top_candidates = candidates[:self.cfg.llm_max_candidates]

        prompt = self._build_prompt(top_candidates, eval_date)

        try:
            response = client.chat.completions.create(
                model=self.cfg.llm_model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            llm_result = json.loads(content)

            return self._merge_scores(candidates, llm_result)

        except Exception as e:
            logger.warning(f"LLM 优选失败: {e}，使用纯量化排序")
            return candidates

    def _system_prompt(self) -> str:
        return """你是一位专业的A股量化策略分析师。你的任务是对量化策略筛选出的候选标的进行二次优选。

你需要从以下维度评估每只标的：
1. 趋势健康度：均线排列、动量一致性、回撤控制
2. 基本面支撑：业绩增长、行业景气、估值合理性
3. 资金面信号：主力资金流入、量价配合
4. 风险评估：追高风险、板块拥挤度、市场情绪

输出格式（JSON）：
{
  "rankings": [
    {
      "ts_code": "代码",
      "llm_score": 1-10的整数评分,
      "confidence": 0.0-1.0的置信度,
      "reason": "一句话理由",
      "risk": "主要风险点"
    }
  ],
  "market_view": "对当前市场环境的简要判断"
}"""

    def _build_prompt(self, candidates: List[Dict], eval_date: str) -> str:
        lines = [f"评估日期: {eval_date}\n"]
        lines.append("以下是量化策略筛选出的候选标的，请进行优选排序：\n")

        for i, c in enumerate(candidates, 1):
            ts_code = c.get('ts_code', '')
            signal_type = c.get('signal_type', 'unknown')
            b_score = c.get('b_score', 0)
            c_score = c.get('c_score', 0)
            e_score = c.get('e_score', 0)

            lines.append(f"\n--- 标的 {i}: {ts_code} ---")
            lines.append(f"信号类型: {'启动型(爆发)' if signal_type == 'launch' else '趋势型(持续)'}")
            lines.append(f"量化评分: B层={b_score:.1f} C层={c_score:.1f} E层={e_score:.1f}")

            b_details = c.get('b_details', {})
            c_details = c.get('c_details', {})
            e_details = c.get('e_details', {})

            if b_details:
                lines.append("B层因子详情:")
                for k, v in b_details.items():
                    if v:
                        lines.append(f"  {k}: {v}")

            if c_details:
                lines.append("C层因子详情:")
                for k, v in c_details.items():
                    if v:
                        lines.append(f"  {k}: {v}")

            if e_details:
                lines.append("E层趋势详情:")
                for k, v in e_details.items():
                    if v:
                        lines.append(f"  {k}: {v}")

        lines.append("\n请对以上标的进行优选排序，返回JSON格式结果。")
        return "\n".join(lines)

    def _merge_scores(self, candidates: List[Dict],
                      llm_result: Dict) -> List[Dict]:
        rankings = llm_result.get('rankings', [])
        llm_map = {r['ts_code']: r for r in rankings}

        for c in candidates:
            ts_code = c.get('ts_code', '')
            quant_score = c.get('composite_score',
                              c.get('c_score', 0) + c.get('b_score', 0) + c.get('e_score', 0))

            if ts_code in llm_map:
                r = llm_map[ts_code]
                llm_score = r.get('llm_score', 5) / 10.0
                confidence = r.get('confidence', 0.5)

                max_quant = max(abs(quant_score), 1)
                norm_quant = quant_score / max_quant

                c['llm_score'] = r.get('llm_score', 5)
                c['llm_confidence'] = confidence
                c['llm_reason'] = r.get('reason', '')
                c['llm_risk'] = r.get('risk', '')
                c['final_score'] = norm_quant * 0.6 + llm_score * confidence * 0.4
            else:
                c['llm_score'] = 0
                c['llm_confidence'] = 0
                c['llm_reason'] = 'LLM未评估'
                c['llm_risk'] = ''
                c['final_score'] = quant_score

        candidates.sort(key=lambda x: x.get('final_score', 0), reverse=True)
        return candidates[:self.cfg.llm_final_top_n]
