"""
报告生成系统核心模块
自动化生成投资分析报告
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Union
import json
import os
import hashlib
from enum import Enum
import logging
from dataclasses import dataclass, field
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # 非交互式后端

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ReportType(Enum):
    """报告类型"""
    DAILY = "daily"            # 日报
    WEEKLY = "weekly"          # 周报
    MONTHLY = "monthly"        # 月报
    QUARTERLY = "quarterly"    # 季报
    YEARLY = "yearly"          # 年报
    STRATEGY = "strategy"      # 策略报告
    RISK = "risk"              # 风险报告
    PERFORMANCE = "performance"  # 绩效报告
    TRADE = "trade"            # 交易报告
    CUSTOM = "custom"          # 自定义报告


class ReportFormat(Enum):
    """报告格式"""
    HTML = "html"              # HTML网页
    MARKDOWN = "markdown"      # Markdown文档
    PDF = "pdf"                # PDF文档
    EXCEL = "excel"            # Excel文件
    JSON = "json"              # JSON数据
    CSV = "csv"                # CSV数据


@dataclass
class ReportTemplate:
    """报告模板"""
    template_id: str
    report_type: ReportType
    format: ReportFormat
    template_content: str
    variables: List[str] = field(default_factory=list)
    style_sheet: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class ReportData:
    """报告数据"""
    report_id: str
    report_type: ReportType
    data: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=datetime.now)


@dataclass
class GeneratedReport:
    """生成的报告"""
    report_id: str
    report_type: ReportType
    format: ReportFormat
    content: Union[str, bytes]  # 字符串或二进制内容
    file_path: Optional[str] = None
    file_size: int = 0
    generated_at: datetime = field(default_factory=datetime.now)


class ReportGenerator:
    """报告生成器"""
    
    def __init__(self, output_dir: str = None):
        if output_dir is None:
            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'reports'
            )
        
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        # 报告模板存储
        self.templates: Dict[str, ReportTemplate] = {}
        
        # 生成报告历史
        self.report_history: List[GeneratedReport] = []
        
        # 初始化默认模板
        self._initialize_default_templates()
        
        logger.info(f"报告生成器初始化完成，输出目录: {output_dir}")
    
    def _initialize_default_templates(self):
        """初始化默认模板"""
        # HTML日报模板
        daily_html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{report_title}} - {{date}}</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .header { background: #f5f5f5; padding: 20px; border-radius: 5px; margin-bottom: 20px; }
        .section { margin-bottom: 30px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }
        .metric-card { background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 3px; }
        .metric-value { font-size: 24px; font-weight: bold; color: #007bff; }
        .metric-label { color: #6c757d; font-size: 14px; }
        .positive { color: #28a745; }
        .negative { color: #dc3545; }
        table { width: 100%; border-collapse: collapse; margin: 10px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .chart-container { margin: 20px 0; text-align: center; }
        .chart-img { max-width: 100%; height: auto; }
        .recommendation { background: #e7f3ff; padding: 15px; border-left: 4px solid #007bff; margin: 10px 0; }
        .alert { background: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>{{report_title}}</h1>
        <p>生成时间: {{generated_at}}</p>
        <p>报告周期: {{period_start}} 至 {{period_end}}</p>
    </div>
    
    <div class="section">
        <h2>📊 投资组合概览</h2>
        <div class="row">
            {{#portfolio_summary}}
            <div class="metric-card" style="display: inline-block; width: 30%; margin: 1%;">
                <div class="metric-label">{{label}}</div>
                <div class="metric-value {{#positive}}positive{{/positive}}{{#negative}}negative{{/negative}}">
                    {{value}}
                </div>
                <div style="font-size: 12px; color: #6c757d;">{{change}}</div>
            </div>
            {{/portfolio_summary}}
        </div>
    </div>
    
    <div class="section">
        <h2>📈 绩效表现</h2>
        <table>
            <tr>
                <th>指标</th>
                <th>数值</th>
                <th>基准</th>
                <th>排名</th>
                <th>变化</th>
            </tr>
            {{#performance_metrics}}
            <tr>
                <td>{{name}}</td>
                <td>{{value}}</td>
                <td>{{benchmark}}</td>
                <td>{{ranking}}</td>
                <td class="{{#positive}}positive{{/positive}}{{#negative}}negative{{/negative}}">
                    {{change}}
                </td>
            </tr>
            {{/performance_metrics}}
        </table>
        
        {{#has_charts}}
        <div class="chart-container">
            <h3>净值曲线</h3>
            <img src="{{nav_chart}}" alt="净值曲线图" class="chart-img">
        </div>
        {{/has_charts}}
    </div>
    
    <div class="section">
        <h2>⚠️ 风险监控</h2>
        <table>
            <tr>
                <th>风险指标</th>
                <th>当前值</th>
                <th>限额</th>
                <th>状态</th>
                <th>建议</th>
            </tr>
            {{#risk_metrics}}
            <tr>
                <td>{{name}}</td>
                <td>{{value}}</td>
                <td>{{limit}}</td>
                <td>
                    <span style="color: {{status_color}}; font-weight: bold;">
                        {{status}}
                    </span>
                </td>
                <td>{{recommendation}}</td>
            </tr>
            {{/risk_metrics}}
        </table>
        
        {{#has_alerts}}
        <div class="alert">
            <h3>🔔 风险告警</h3>
            <ul>
                {{#alerts}}
                <li>{{message}} ({{timestamp}})</li>
                {{/alerts}}
            </ul>
        </div>
        {{/has_alerts}}
    </div>
    
    <div class="section">
        <h2>💼 持仓分析</h2>
        <table>
            <tr>
                <th>代码</th>
                <th>名称</th>
                <th>持仓数量</th>
                <th>市值</th>
                <th>权重</th>
                <th>成本价</th>
                <th>当前价</th>
                <th>盈亏</th>
                <th>盈亏%</th>
            </tr>
            {{#positions}}
            <tr>
                <td>{{symbol}}</td>
                <td>{{name}}</td>
                <td>{{quantity}}</td>
                <td>{{market_value}}</td>
                <td>{{weight}}</td>
                <td>{{avg_cost}}</td>
                <td>{{current_price}}</td>
                <td class="{{#profit_positive}}positive{{/profit_positive}}{{#profit_negative}}negative{{/profit_negative}}">
                    {{pnl}}
                </td>
                <td class="{{#profit_positive}}positive{{/profit_positive}}{{#profit_negative}}negative{{/profit_negative}}">
                    {{pnl_percent}}
                </td>
            </tr>
            {{/positions}}
        </table>
        
        <div style="margin-top: 20px;">
            <h4>行业分布</h4>
            <ul>
                {{#sector_distribution}}
                <li>{{sector}}: {{weight}}% ({{count}}只股票)</li>
                {{/sector_distribution}}
            </ul>
        </div>
    </div>
    
    <div class="section">
        <h2>🔄 交易活动</h2>
        <table>
            <tr>
                <th>时间</th>
                <th>代码</th>
                <th>方向</th>
                <th>数量</th>
                <th>价格</th>
                <th>金额</th>
                <th>手续费</th>
                <th>策略</th>
            </tr>
            {{#recent_trades}}
            <tr>
                <td>{{timestamp}}</td>
                <td>{{symbol}}</td>
                <td>
                    <span style="color: {{#is_buy}}#28a745{{/is_buy}}{{#is_sell}}#dc3545{{/is_sell}}; font-weight: bold;">
                        {{side}}
                    </span>
                </td>
                <td>{{quantity}}</td>
                <td>{{price}}</td>
                <td>{{amount}}</td>
                <td>{{commission}}</td>
                <td>{{strategy}}</td>
            </tr>
            {{/recent_trades}}
        </table>
        
        <div class="metric-card">
            <div class="metric-label">当日交易统计</div>
            <div style="display: flex; justify-content: space-between;">
                <div>
                    <div class="metric-label">总交易次数</div>
                    <div class="metric-value">{{trade_stats.total_trades}}</div>
                </div>
                <div>
                    <div class="metric-label">买入次数</div>
                    <div class="metric-value positive">{{trade_stats.buy_trades}}</div>
                </div>
                <div>
                    <div class="metric-label">卖出次数</div>
                    <div class="metric-value negative">{{trade_stats.sell_trades}}</div>
                </div>
                <div>
                    <div class="metric-label">胜率</div>
                    <div class="metric-value">{{trade_stats.win_rate}}</div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="section">
        <h2>💡 投资建议</h2>
        {{#recommendations}}
        <div class="recommendation">
            <h4>{{title}}</h4>
            <p>{{description}}</p>
            <p><strong>建议操作:</strong> {{action}}</p>
            <p><small>优先级: {{priority}} | 预期影响: {{impact}}</small></p>
        </div>
        {{/recommendations}}
        
        {{^recommendations}}
        <p>暂无投资建议。</p>
        {{/recommendations}}
    </div>
    
    <div class="section">
        <h2>📋 附件</h2>
        <ul>
            {{#attachments}}
            <li><a href="{{url}}">{{name}}</a> ({{size}})</li>
            {{/attachments}}
        </ul>
    </div>
    
    <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #6c757d;">
        <p>报告生成系统: Quant System Reporting Engine v1.0</p>
        <p>数据截止时间: {{data_cutoff_time}}</p>
        <p>免责声明: 本报告仅供参考，不构成投资建议。投资有风险，入市需谨慎。</p>
    </div>
</body>
</html>
        """
        
        # Markdown日报模板
        daily_markdown_template = """# {{report_title}}

**生成时间**: {{generated_at}}  
**报告周期**: {{period_start}} 至 {{period_end}}

---

## 📊 投资组合概览

{{#portfolio_summary}}
- **{{label}}**: {{value}} ({{change}})
{{/portfolio_summary}}

---

## 📈 绩效表现

| 指标 | 数值 | 基准 | 排名 | 变化 |
|------|------|------|------|------|
{{#performance_metrics}}
| {{name}} | {{value}} | {{benchmark}} | {{ranking}} | {{change}} |
{{/performance_metrics}}

{{#has_charts}}
![净值曲线]({{nav_chart}})
{{/has_charts}}

---

## ⚠️ 风险监控

| 风险指标 | 当前值 | 限额 | 状态 | 建议 |
|----------|--------|------|------|------|
{{#risk_metrics}}
| {{name}} | {{value}} | {{limit}} | {{status}} | {{recommendation}} |
{{/risk_metrics}}

{{#has_alerts}}
### 🔔 风险告警
{{#alerts}}
- {{message}} ({{timestamp}})
{{/alerts}}
{{/has_alerts}}

---

## 💼 持仓分析

| 代码 | 名称 | 持仓数量 | 市值 | 权重 | 成本价 | 当前价 | 盈亏 | 盈亏% |
|------|------|----------|------|------|--------|--------|------|-------|
{{#positions}}
| {{symbol}} | {{name}} | {{quantity}} | {{market_value}} | {{weight}} | {{avg_cost}} | {{current_price}} | {{pnl}} | {{pnl_percent}} |
{{/positions}}

### 行业分布
{{#sector_distribution}}
- {{sector}}: {{weight}}% ({{count}}只股票)
{{/sector_distribution}}

---

## 🔄 交易活动

| 时间 | 代码 | 方向 | 数量 | 价格 | 金额 | 手续费 | 策略 |
|------|------|------|------|------|------|--------|------|
{{#recent_trades}}
| {{timestamp}} | {{symbol}} | {{side}} | {{quantity}} | {{price}} | {{amount}} | {{commission}} | {{strategy}} |
{{/recent_trades}}

### 当日交易统计
- **总交易次数**: {{trade_stats.total_trades}}
- **买入次数**: {{trade_stats.buy_trades}}
- **卖出次数**: {{trade_stats.sell_trades}}
- **胜率**: {{trade_stats.win_rate}}

---

## 💡 投资建议

{{#recommendations}}
### {{title}}
{{description}}

**建议操作**: {{action}}  
**优先级**: {{priority}} | **预期影响**: {{impact}}

{{/recommendations}}

{{^recommendations}}
暂无投资建议。
{{/recommendations}}

---

## 📋 附件

{{#attachments}}
- [{{name}}]({{url}}) ({{size}})
{{/attachments}}

---

*报告生成系统: Quant System Reporting Engine v1.0*  
*数据截止时间: {{data_cutoff_time}}*  
*免责声明: 本报告仅供参考，不构成投资建议。投资有风险，入市需谨慎。*
        """
        
        # 注册默认模板
        self.register_template(
            template_id="daily_html",
            report_type=ReportType.DAILY,
            format=ReportFormat.HTML,
            template_content=daily_html_template,
            variables=[
                'report_title', 'date', 'generated_at', 'period_start', 'period_end',
                'portfolio_summary', 'performance_metrics', 'risk_metrics', 'alerts',
                'positions', 'sector_distribution', 'recent_trades', 'trade_stats',
                'recommendations', 'attachments', 'data_cutoff_time',
                'has_charts', 'nav_chart', 'has_alerts'
            ]
        )
        
        self.register_template(
            template_id="daily_markdown",
            report_type=ReportType.DAILY,
            format=ReportFormat.MARKDOWN,
            template_content=daily_markdown_template,
            variables=[
                'report_title', 'generated_at', 'period_start', 'period_end',
                'portfolio_summary', 'performance_metrics', 'risk_metrics', 'alerts',
                'positions', 'sector_distribution', 'recent_trades', 'trade_stats',
                'recommendations', 'attachments', 'data_cutoff_time',
                'has_charts', 'nav_chart', 'has_alerts'
            ]
        )
        
        logger.info("默认报告模板初始化完成")
    
    def register_template(self, template_id: str, report_type: ReportType,
                         format: ReportFormat, template_content: str,
                         variables: List[str] = None, style_sheet: str = None):
        """注册报告模板"""
        template = ReportTemplate(
            template_id=template_id,
            report_type=report_type,
            format=format,
            template_content=template_content,
            variables=variables or [],
            style_sheet=style_sheet
        )
        
        self.templates[template_id] = template
        logger.info(f"注册报告模板: {template_id} ({report_type.value}.{format.value})")
    
    def generate_report(self, report_type: ReportType, data: Dict[str, Any],
                       format: ReportFormat = ReportFormat.HTML,
                       template_id: Optional[str] = None) -> GeneratedReport:
        """生成报告"""
        # 查找模板
        template = self._find_template(report_type, format, template_id)
        if not template:
            raise ValueError(f"找不到合适的模板: {report_type.value}.{format.value}")
        
        # 生成报告ID
        report_id = self._generate_report_id(report_type, data)
        
        # 准备数据
        prepared_data = self._prepare_report_data(data, template)
        
        # 渲染报告
        if format == ReportFormat.HTML:
            content = self._render_html(template, prepared_data)
            file_ext = '.html'
        elif format == ReportFormat.MARKDOWN:
            content = self._render_markdown(template, prepared_data)
            file_ext = '.md'
        elif format == ReportFormat.JSON:
            content = json.dumps(prepared_data, indent=2, ensure_ascii=False, default=str)
            file_ext = '.json'
        elif format == ReportFormat.CSV:
            content = self._render_csv(prepared_data)
            file_ext = '.csv'
        else:
            # 默认使用HTML
            content = self._render_html(template, prepared_data)
            file_ext = '.html'
        
        # 保存文件
        filename = f"{report_type.value}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}{file_ext}"
        file_path = os.path.join(self.output_dir, filename)
        
        # 写入文件
        if isinstance(content, str):
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            file_size = os.path.getsize(file_path)
        else:
            with open(file_path, 'wb') as f:
                f.write(content)
            file_size = os.path.getsize(file_path)
        
        # 创建报告对象
        report = GeneratedReport(
            report_id=report_id,
            report_type=report_type,
            format=format,
            content=content,
            file_path=file_path,
            file_size=file_size
        )
        
        # 保存到历史
        self.report_history.append(report)
        
        logger.info(f"报告生成完成: {report_id} -> {file_path} ({file_size}字节)")
        
        return report
    
    def _find_template(self, report_type: ReportType, format: ReportFormat,
                      template_id: Optional[str] = None) -> Optional[ReportTemplate]:
        """查找模板"""
        if template_id and template_id in self.templates:
            return self.templates[template_id]
        
        # 查找匹配类型和格式的模板
        for template in self.templates.values():
            if template.report_type == report_type and template.format == format:
                return template
        
        # 如果没有找到，尝试使用默认模板
        default_template_id = f"{report_type.value}_{format.value}"
        if default_template_id in self.templates:
            return self.templates[default_template_id]
        
        return None
    
    def _generate_report_id(self, report_type: ReportType, data: Dict[str, Any]) -> str:
        """生成报告ID"""
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        data_hash = hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()[:8]
        return f"report_{report_type.value}_{timestamp}_{data_hash}"
    
    def _prepare_report_data(self, data: Dict[str, Any], template: ReportTemplate) -> Dict[str, Any]:
        """准备报告数据"""
        prepared_data = data.copy()
        
        # 确保必要字段存在
        prepared_data.setdefault('report_title', f"{template.report_type.value.title()} Report")
        prepared_data.setdefault('generated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        prepared_data.setdefault('period_start', (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'))
        prepared_data.setdefault('period_end', datetime.now().strftime('%Y-%m-%d'))
        prepared_data.setdefault('data_cutoff_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        # 处理投资组合摘要
        if 'portfolio_summary' not in prepared_data:
            prepared_data['portfolio_summary'] = self._generate_default_portfolio_summary()
        
        # 处理绩效指标
        if 'performance_metrics' not in prepared_data:
            prepared_data['performance_metrics'] = self._generate_default_performance_metrics()
        
        # 处理风险指标
        if 'risk_metrics' not in prepared_data:
            prepared_data['risk_metrics'] = self._generate_default_risk_metrics()
        
        # 处理持仓数据
        if 'positions' not in prepared_data:
            prepared_data['positions'] = self._generate_default_positions()
        
        # 处理交易数据
        if 'recent_trades' not in prepared_data:
            prepared_data['recent_trades'] = self._generate_default_trades()
        
        # 处理投资建议
        if 'recommendations' not in prepared_data:
            prepared_data['recommendations'] = self._generate_default_recommendations()
        
        # 处理附件
        if 'attachments' not in prepared_data:
            prepared_data['attachments'] = []
        
        # 添加标志字段
        prepared_data['has_charts'] = prepared_data.get('has_charts', False)
        prepared_data['has_alerts'] = prepared_data.get('has_alerts', False)
        
        return prepared_data
    
    def _generate_default_portfolio_summary(self) -> List[Dict[str, Any]]:
        """生成默认投资组合摘要"""
        return [
            {'label': '总资产', 'value': '1,234,567', 'change': '+2.34%', 'positive': True},
            {'label': '现金', 'value': '234,567', 'change': '-1.23%', 'negative': True},
            {'label': '持仓市值', 'value': '1,000,000', 'change': '+3.45%', 'positive': True},
            {'label': '当日盈亏', 'value': '+23,456', 'change': '+1.89%', 'positive': True},
            {'label': '累计盈亏', 'value': '+123,456', 'change': '+11.23%', 'positive': True},
            {'label': '夏普比率', 'value': '1.85', 'change': '+0.12', 'positive': True}
        ]
    
    def _generate_default_performance_metrics(self) -> List[Dict[str, Any]]:
        """生成默认绩效指标"""
        return [
            {'name': '年化收益', 'value': '15.23%', 'benchmark': '8.45%', 'ranking': '1/10', 'change': '+2.34%', 'positive': True},
            {'name': '夏普比率', 'value': '1.85', 'benchmark': '1.20', 'ranking': '2/10', 'change': '+0.12', 'positive': True},
            {'name': '最大回撤', 'value': '-12.34%', 'benchmark': '-15.67%', 'ranking': '3/10', 'change': '-1.23%', 'positive': True},
            {'name': '波动率', 'value': '18.23%', 'benchmark': '20.45%', 'ranking': '4/10', 'change': '-0.56%', 'positive': True},
            {'name': '胜率', 'value': '65.43%', 'benchmark': '55.67%', 'ranking': '2/10', 'change': '+3.21%', 'positive': True},
            {'name': '盈亏比', 'value': '1.85', 'benchmark': '1.45', 'ranking': '1/10', 'change': '+0.23', 'positive': True}
        ]
    
    def _generate_default_risk_metrics(self) -> List[Dict[str, Any]]:
        """生成默认风险指标"""
        return [
            {'name': 'VaR (95%)', 'value': '-4.56%', 'limit': '-5.00%', 'status': '正常', 'status_color': '#28a745', 'recommendation': '保持监控'},
            {'name': 'CVaR (95%)', 'value': '-6.78%', 'limit': '-7.50%', 'status': '正常', 'status_color': '#28a745', 'recommendation': '保持监控'},
            {'name': '最大回撤', 'value': '-12.34%', 'limit': '-20.00%', 'status': '正常', 'status_color': '#28a745', 'recommendation': '设置止损'},
            {'name': '集中度风险', 'value': '0.45', 'limit': '0.60', 'status': '正常', 'status_color': '#28a745', 'recommendation': '适当分散'},
            {'name': '杠杆率', 'value': '1.23', 'limit': '2.00', 'status': '正常', 'status_color': '#28a745', 'recommendation': '保持现状'}
        ]
    
    def _generate_default_positions(self) -> List[Dict[str, Any]]:
        """生成默认持仓数据"""
        return [
            {'symbol': '600519', 'name': '贵州茅台', 'quantity': 100, 'market_value': '165,000', 'weight': '16.50%', 
             'avg_cost': '1,500.00', 'current_price': '1,650.00', 'pnl': '+15,000', 'pnl_percent': '+10.00%', 'profit_positive': True},
            {'symbol': '000858', 'name': '五粮液', 'quantity': 200, 'market_value': '120,000', 'weight': '12.00%', 
             'avg_cost': '550.00', 'current_price': '600.00', 'pnl': '+10,000', 'pnl_percent': '+9.09%', 'profit_positive': True},
            {'symbol': '000333', 'name': '美的集团', 'quantity': 300, 'market_value': '90,000', 'weight': '9.00%', 
             'avg_cost': '280.00', 'current_price': '300.00', 'pnl': '+6,000', 'pnl_percent': '+7.14%', 'profit_positive': True},
            {'symbol': '000001', 'name': '平安银行', 'quantity': 500, 'market_value': '75,000', 'weight': '7.50%', 
             'avg_cost': '14.00', 'current_price': '15.00', 'pnl': '+500', 'pnl_percent': '+7.14%', 'profit_positive': True},
            {'symbol': '002415', 'name': '海康威视', 'quantity': 400, 'market_value': '60,000', 'weight': '6.00%', 
             'avg_cost': '14.50', 'current_price': '15.00', 'pnl': '+200', 'pnl_percent': '+3.45%', 'profit_positive': True}
        ]
    
    def _generate_default_trades(self) -> List[Dict[str, Any]]:
        """生成默认交易数据"""
        return [
            {'timestamp': '09:30:15', 'symbol': '600519', 'side': '买入', 'is_buy': True, 'quantity': 50, 
             'price': '1,645.00', 'amount': '82,250', 'commission': '123.38', 'strategy': '价值投资'},
            {'timestamp': '10:15:23', 'symbol': '000858', 'side': '买入', 'is_buy': True, 'quantity': 100, 
             'price': '598.00', 'amount': '59,800', 'commission': '89.70', 'strategy': '趋势跟踪'},
            {'timestamp': '13:45:12', 'symbol': '000333', 'side': '卖出', 'is_sell': True, 'quantity': 150, 
             'price': '302.00', 'amount': '45,300', 'commission': '67.95', 'strategy': '获利了结'},
            {'timestamp': '14:30:45', 'symbol': '000001', 'side': '买入', 'is_buy': True, 'quantity': 200, 
             'price': '14.95', 'amount': '2,990', 'commission': '4.49', 'strategy': '均值回归'}
        ]
    
    def _generate_default_recommendations(self) -> List[Dict[str, Any]]:
        """生成默认投资建议"""
        return [
            {'title': '增加科技股配置', 'description': 'AI行业发展迅速，建议增加科技股配置比例', 
             'action': '买入600536、002230等科技股', 'priority': '高', 'impact': '中等'},
            {'title': '降低消费股集中度', 'description': '消费股持仓过于集中，建议适当分散', 
             'action': '部分减持600519，增持其他行业', 'priority': '中', 'impact': '中等'},
            {'title': '设置止损位', 'description': '市场波动加大，建议为高风险持仓设置止损', 
             'action': '为002415设置15%止损位', 'priority': '高', 'impact': '高'}
        ]
    
    def _render_html(self, template: ReportTemplate, data: Dict[str, Any]) -> str:
        """渲染HTML报告"""
        content = template.template_content
        
        # 简单模板渲染（实际应该使用Jinja2等模板引擎）
        for key, value in data.items():
            if isinstance(value, list):
                # 处理列表数据（简化处理）
                if key == 'portfolio_summary':
                    items_html = []
                    for item in value:
                        item_html = []
                        for k, v in item.items():
                            item_html.append(f'{k}="{v}"' if isinstance(v, str) else f'{k}={v}')
                        items_html.append(f'<div class="metric-card" {">".join(item_html)}></div>')
                    content = content.replace(f'{{{{#{key}}}}}', '\n'.join(items_html))
                    content = content.replace(f'{{{{/{key}}}}}', '')
                else:
                    # 简化处理：转换为JSON字符串
                    content = content.replace(f'{{{{{key}}}}}', json.dumps(value, ensure_ascii=False))
            else:
                # 简单变量替换
                placeholder = f'{{{{{key}}}}}'
                if placeholder in content:
                    content = content.replace(placeholder, str(value))
        
        # 移除未使用的模板标签
        import re
        content = re.sub(r'\{\{#.*?#\}\}', '', content, flags=re.DOTALL)
        content = re.sub(r'\{\{/.*?\}\}', '', content)
        content = re.sub(r'\{\{.*?\}\}', '', content)
        
        return content
    
    def _render_markdown(self, template: ReportTemplate, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        content = template.template_content
        
        # 简单模板渲染
        for key, value in data.items():
            if isinstance(value, list):
                # 处理列表数据
                if key in ['portfolio_summary', 'performance_metrics', 'risk_metrics', 
                          'positions', 'recent_trades', 'recommendations']:
                    # 简化处理：转换为JSON字符串
                    content = content.replace(f'{{{{{key}}}}}', json.dumps(value, ensure_ascii=False))
                else:
                    content = content.replace(f'{{{{{key}}}}}', json.dumps(value, ensure_ascii=False))
            else:
                # 简单变量替换
                placeholder = f'{{{{{key}}}}}'
                if placeholder in content:
                    content = content.replace(placeholder, str(value))
        
        # 移除未使用的模板标签
        import re
        content = re.sub(r'\{\{#.*?#\}\}', '', content, flags=re.DOTALL)
        content = re.sub(r'\{\{/.*?\}\}', '', content)
        content = re.sub(r'\{\{.*?\}\}', '', content)
        
        return content
    
    def _render_csv(self, data: Dict[str, Any]) -> str:
        """渲染CSV报告"""
        # 简化实现：将主要表格数据转换为CSV
        csv_lines = []
        
        # 添加标题
        csv_lines.append(f"报告标题,{data.get('report_title', '')}")
        csv_lines.append(f"生成时间,{data.get('generated_at', '')}")
        csv_lines.append(f"报告周期,{data.get('period_start', '')} 至 {data.get('period_end', '')}")
        csv_lines.append("")
        
        # 投资组合摘要
        if 'portfolio_summary' in data and data['portfolio_summary']:
            csv_lines.append("投资组合摘要")
            csv_lines.append("指标,数值,变化")
            for item in data['portfolio_summary']:
                csv_lines.append(f"{item.get('label', '')},{item.get('value', '')},{item.get('change', '')}")
            csv_lines.append("")
        
        # 持仓数据
        if 'positions' in data and data['positions']:
            csv_lines.append("持仓明细")
            if data['positions']:
                headers = list(data['positions'][0].keys())
                csv_lines.append(','.join(headers))
                for position in data['positions']:
                    row = [str(position.get(h, '')) for h in headers]
                    csv_lines.append(','.join(row))
            csv_lines.append("")
        
        # 交易记录
        if 'recent_trades' in data and data['recent_trades']:
            csv_lines.append("交易记录")
            if data['recent_trades']:
                headers = list(data['recent_trades'][0].keys())
                csv_lines.append(','.join(headers))
                for trade in data['recent_trades']:
                    row = [str(trade.get(h, '')) for h in headers]
                    csv_lines.append(','.join(row))
            csv_lines.append("")
        
        return '\n'.join(csv_lines)
    
    def generate_daily_report(self, trading_data: Dict[str, Any] = None,
                             risk_data: Dict[str, Any] = None,
                             portfolio_data: Dict[str, Any] = None,
                             format: ReportFormat = ReportFormat.HTML) -> GeneratedReport:
        """生成日报"""
        # 合并数据
        data = {
            'report_title': '每日投资报告',
            'report_type': 'daily',
            'date': datetime.now().strftime('%Y年%m月%d日'),
            **self._prepare_daily_data(trading_data, risk_data, portfolio_data)
        }
        
        return self.generate_report(ReportType.DAILY, data, format)
    
    def generate_risk_report(self, risk_data: Dict[str, Any],
                            format: ReportFormat = ReportFormat.HTML) -> GeneratedReport:
        """生成风险报告"""
        data = {
            'report_title': '风险监控报告',
            'report_type': 'risk',
            'date': datetime.now().strftime('%Y年%m月%d日'),
            **risk_data
        }
        
        return self.generate_report(ReportType.RISK, data, format)
    
    def generate_performance_report(self, performance_data: Dict[str, Any],
                                  format: ReportFormat = ReportFormat.HTML) -> GeneratedReport:
        """生成绩效报告"""
        data = {
            'report_title': '投资绩效报告',
            'report_type': 'performance',
            'date': datetime.now().strftime('%Y年%m月%d日'),
            **performance_data
        }
        
        return self.generate_report(ReportType.PERFORMANCE, data, format)
    
    def _prepare_daily_data(self, trading_data: Dict[str, Any] = None,
                           risk_data: Dict[str, Any] = None,
                           portfolio_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """准备日报数据"""
        data = {}
        
        # 合并交易数据
        if trading_data:
            data.update({
                'recent_trades': trading_data.get('recent_trades', []),
                'trade_stats': trading_data.get('trade_stats', {})
            })
        
        # 合并风险数据
        if risk_data:
            data.update({
                'risk_metrics': risk_data.get('risk_metrics', []),
                'alerts': risk_data.get('alerts', []),
                'has_alerts': len(risk_data.get('alerts', [])) > 0
            })
        
        # 合并投资组合数据
        if portfolio_data:
            data.update({
                'portfolio_summary': portfolio_data.get('portfolio_summary', []),
                'positions': portfolio_data.get('positions', []),
                'sector_distribution': portfolio_data.get('sector_distribution', []),
                'performance_metrics': portfolio_data.get('performance_metrics', [])
            })
        
        return data
    
    def get_report_history(self, report_type: Optional[ReportType] = None,
                          limit: int = 100) -> List[GeneratedReport]:
        """获取报告历史"""
        reports = self.report_history
        
        if report_type:
            reports = [r for r in reports if r.report_type == report_type]
        
        return reports[-limit:] if limit else reports
    
    def delete_report(self, report_id: str) -> bool:
        """删除报告"""
        for i, report in enumerate(self.report_history):
            if report.report_id == report_id:
                # 删除文件
                if report.file_path and os.path.exists(report.file_path):
                    os.remove(report.file_path)
                
                # 从历史中移除
                self.report_history.pop(i)
                logger.info(f"报告已删除: {report_id}")
                return True
        
        return False


# 图表生成器
class ChartGenerator:
    """图表生成器"""
    
    def __init__(self, output_dir: str = None):
        if output_dir is None:
            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'reports', 'charts'
            )
        
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
    
    def generate_nav_chart(self, nav_data: pd.DataFrame, benchmark_data: pd.DataFrame = None,
                          title: str = "净值曲线") -> str:
        """生成净值曲线图"""
        try:
            plt.figure(figsize=(12, 6))
            
            # 绘制净值曲线
            if not nav_data.empty:
                plt.plot(nav_data.index, nav_data.values, label='策略净值', linewidth=2)
            
            # 绘制基准曲线
            if benchmark_data is not None and not benchmark_data.empty:
                plt.plot(benchmark_data.index, benchmark_data.values, label='基准净值', 
                        linewidth=2, linestyle='--', alpha=0.7)
            
            plt.title(title, fontsize=14, fontweight='bold')
            plt.xlabel('日期', fontsize=12)
            plt.ylabel('净值', fontsize=12)
            plt.grid(True, alpha=0.3)
            plt.legend()
            
            # 保存图表
            filename = f"nav_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = os.path.join(self.output_dir, filename)
            plt.tight_layout()
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            plt.close()
            
            return filepath
            
        except Exception as e:
            logger.error(f"生成净值曲线图失败: {e}")
            return ""
    
    def generate_returns_distribution(self, returns: pd.Series, 
                                     title: str = "收益分布") -> str:
        """生成收益分布图"""
        try:
            plt.figure(figsize=(10, 6))
            
            # 绘制直方图
            plt.hist(returns.dropna(), bins=50, alpha=0.7, edgecolor='black')
            
            # 添加均值和标准差线
            mean_return = returns.mean()
            std_return = returns.std()
            
            plt.axvline(mean_return, color='red', linestyle='--', linewidth=2, 
                       label=f'均值: {mean_return:.2%}')
            plt.axvline(mean_return + std_return, color='orange', linestyle=':', 
                       linewidth=1.5, alpha=0.7)
            plt.axvline(mean_return - std_return, color='orange', linestyle=':', 
                       linewidth=1.5, alpha=0.7)
            
            plt.title(title, fontsize=14, fontweight='bold')
            plt.xlabel('收益率', fontsize=12)
            plt.ylabel('频数', fontsize=12)
            plt.grid(True, alpha=0.3)
            plt.legend()
            
            # 保存图表
            filename = f"returns_dist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = os.path.join(self.output_dir, filename)
            plt.tight_layout()
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            plt.close()
            
            return filepath
            
        except Exception as e:
            logger.error(f"生成收益分布图失败: {e}")
            return ""
    
    def generate_drawdown_chart(self, drawdown_series: pd.Series,
                               title: str = "回撤分析") -> str:
        """生成回撤分析图"""
        try:
            plt.figure(figsize=(12, 6))
            
            # 绘制回撤曲线
            plt.fill_between(drawdown_series.index, 0, drawdown_series.values, 
                           alpha=0.3, color='red')
            plt.plot(drawdown_series.index, drawdown_series.values, 
                    color='red', linewidth=1.5)
            
            # 标记最大回撤
            max_dd = drawdown_series.min()
            max_dd_date = drawdown_series.idxmin()
            
            plt.axhline(max_dd, color='darkred', linestyle='--', linewidth=1.5, 
                       alpha=0.7, label=f'最大回撤: {max_dd:.2%}')
            plt.axvline(max_dd_date, color='darkred', linestyle=':', linewidth=1.5, 
                       alpha=0.5)
            
            plt.title(title, fontsize=14, fontweight='bold')
            plt.xlabel('日期', fontsize=12)
            plt.ylabel('回撤', fontsize=12)
            plt.grid(True, alpha=0.3)
            plt.legend()
            
            # 保存图表
            filename = f"drawdown_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = os.path.join(self.output_dir, filename)
            plt.tight_layout()
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            plt.close()
            
            return filepath
            
        except Exception as e:
            logger.error(f"生成回撤分析图失败: {e}")
            return ""
    
    def generate_sector_allocation(self, sector_data: Dict[str, float],
                                  title: str = "行业配置") -> str:
        """生成行业配置饼图"""
        try:
            if not sector_data:
                return ""
            
            plt.figure(figsize=(10, 8))
            
            # 准备数据
            sectors = list(sector_data.keys())
            weights = list(sector_data.values())
            
            # 绘制饼图
            colors = plt.cm.Set3(np.linspace(0, 1, len(sectors)))
            plt.pie(weights, labels=sectors, autopct='%1.1f%%', startangle=90,
                   colors=colors, textprops={'fontsize': 10})
            
            plt.title(title, fontsize=14, fontweight='bold')
            
            # 保存图表
            filename = f"sector_allocation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = os.path.join(self.output_dir, filename)
            plt.tight_layout()
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            plt.close()
            
            return filepath
            
        except Exception as e:
            logger.error(f"生成行业配置图失败: {e}")
            return ""


# 报告调度器
class ReportScheduler:
    """报告调度器"""
    
    def __init__(self, report_generator: ReportGenerator):
        self.report_generator = report_generator
        self.scheduled_tasks = []
        
        logger.info("报告调度器初始化完成")
    
    def schedule_daily_report(self, hour: int = 18, minute: int = 0):
        """安排每日报告"""
        task = {
            'type': 'daily',
            'hour': hour,
            'minute': minute,
            'last_run': None,
            'next_run': self._calculate_next_run(hour, minute)
        }
        
        self.scheduled_tasks.append(task)
        logger.info(f"安排每日报告: {hour:02d}:{minute:02d}")
    
    def schedule_weekly_report(self, weekday: int = 0, hour: int = 18, minute: int = 0):
        """安排每周报告（weekday: 0=周一, 6=周日）"""
        task = {
            'type': 'weekly',
            'weekday': weekday,
            'hour': hour,
            'minute': minute,
            'last_run': None,
            'next_run': self._calculate_next_weekly_run(weekday, hour, minute)
        }
        
        self.scheduled_tasks.append(task)
        logger.info(f"安排每周报告: 星期{weekday+1} {hour:02d}:{minute:02d}")
    
    def _calculate_next_run(self, hour: int, minute: int) -> datetime:
        """计算下次运行时间"""
        now = datetime.now()
        today_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        if now < today_run:
            return today_run
        else:
            return today_run + timedelta(days=1)
    
    def _calculate_next_weekly_run(self, weekday: int, hour: int, minute: int) -> datetime:
        """计算下次每周运行时间"""
        now = datetime.now()
        today_weekday = now.weekday()  # 0=周一, 6=周日
        
        days_ahead = weekday - today_weekday
        if days_ahead < 0:
            days_ahead += 7
        
        next_date = now + timedelta(days=days_ahead)
        return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    def check_and_run(self):
        """检查并运行计划任务"""
        now = datetime.now()
        
        for task in self.scheduled_tasks:
            if task['next_run'] and now >= task['next_run']:
                try:
                    self._run_scheduled_task(task)
                    task['last_run'] = now
                    
                    # 更新下次运行时间
                    if task['type'] == 'daily':
                        task['next_run'] = self._calculate_next_run(task['hour'], task['minute'])
                    elif task['type'] == 'weekly':
                        task['next_run'] = self._calculate_next_weekly_run(
                            task['weekday'], task['hour'], task['minute']
                        )
                    
                    logger.info(f"执行计划任务: {task['type']}报告")
                    
                except Exception as e:
                    logger.error(f"执行计划任务失败: {e}")
    
    def _run_scheduled_task(self, task: Dict[str, Any]):
        """运行计划任务"""
        if task['type'] == 'daily':
            self.report_generator.generate_daily_report(format=ReportFormat.HTML)
        elif task['type'] == 'weekly':
            # 生成周报
            data = {
                'report_title': '每周投资报告',
                'period_start': (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'),
                'period_end': datetime.now().strftime('%Y-%m-%d')
            }
            self.report_generator.generate_report(ReportType.WEEKLY, data, ReportFormat.HTML)