"""AI Stock Recommendation - Web UI"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import pandas as pd
import streamlit as st
from datetime import date, datetime
import json

from core.db.connection import get_db
from core.utils.env import load_project_env

load_project_env()

st.set_page_config(page_title="AI 股票推荐系统", layout="wide", page_icon="📊")


@st.cache_data(ttl=60)
def query_df(sql, params=None):
    conn = get_db()
    df = pd.read_sql(sql, conn, params=params)
    conn.close()
    return df


st.sidebar.title("📊 AI 股票推荐")
st.sidebar.markdown("---")
page = st.sidebar.radio("导航", ["📈 今日候选", "🔍 信号提取", "📡 数据源", "📰 原始资讯"])

st.title("AI 股票推荐系统")

if page == "📈 今日候选":
    st.header("今日候选股")
    
    selected_date = st.date_input("选择日期", value=date.today())

    col_filter1, col_filter2 = st.columns(2)
    with col_filter1:
        source_options = {
            "🤖 LLM 多源策略": "llm_multisource",
            "🔮 八步法": "overnight_8step",
        }
        selected_sources = st.multiselect(
            "策略来源",
            list(source_options.keys()),
            default=list(source_options.keys()),
        )
        source_values = [source_options[s] for s in selected_sources]
    with col_filter2:
        run_mode_options = {"盘前(morning)": "morning", "盘中(intraday)": "intraday", "盘后(afternoon)": "afternoon"}
        selected_modes = st.multiselect(
            "运行模式",
            list(run_mode_options.keys()),
            default=list(run_mode_options.keys()),
        )
        mode_values = [run_mode_options[m] for m in selected_modes]

    if not source_values or not mode_values:
        st.warning("请至少选择一个策略来源和运行模式")
        st.stop()

    df = query_df("""
        SELECT ts_code, stock_name, final_score, llm_score, quant_score,
               consensus_score, mention_count, source_diversity,
               selected, position_pct, entry_low, entry_high, stop_loss,
               target_1, target_2, logic_tags, source, run_mode
        FROM daily_candidates
        WHERE snapshot_date = %s
          AND source = ANY(%s)
          AND run_mode = ANY(%s)
        ORDER BY final_score DESC;
    """, (selected_date, source_values, mode_values))
    
    if df.empty:
        st.info(f"{selected_date} 暂无候选数据")
    else:
        def highlight_selected(row):
            if row['selected']:
                return ['background-color: #d4edda'] * len(row)
            return [''] * len(row)
        
        source_labels = {"llm_multisource": "🤖 LLM", "overnight_8step": "🔮 八步法"}
        mode_labels = {"morning": "盘前", "intraday": "盘中", "afternoon": "盘后"}
        display_df = df.copy()
        display_df["来源"] = display_df["source"].map(source_labels).fillna(display_df["source"])
        display_df["模式"] = display_df["run_mode"].map(mode_labels).fillna(display_df["run_mode"])
        display_df["position_pct"] = display_df["position_pct"].fillna(0) * 100

        st.dataframe(
            display_df.style.apply(highlight_selected, axis=1),
            use_container_width=True,
            height=600,
            column_config={
                "ts_code": "代码",
                "stock_name": "名称",
                "final_score": st.column_config.NumberColumn("综合分", format="%.1f"),
                "llm_score": st.column_config.NumberColumn("LLM分", format="%.1f"),
                "quant_score": st.column_config.NumberColumn("量化分", format="%.1f"),
                "selected": "已选中",
                "position_pct": st.column_config.NumberColumn("仓位%", format="%.0f%%"),
                "来源": "策略来源",
                "模式": "运行模式",
                "source": None,
                "run_mode": None,
            }
        )
        
        st.subheader("候选详情")
        for _, row in df.iterrows():
            with st.expander(f"{row['ts_code']} {row['stock_name']} - 综合分: {row['final_score']:.1f}"):
                col1, col2, col3 = st.columns(3)
                col1.metric("LLM 分数", f"{row['llm_score']:.1f}")
                col2.metric("量化分数", f"{row['quant_score']:.1f}")
                col3.metric("共识度", f"{row['consensus_score']:.2f}")
                
                col4, col5, col6 = st.columns(3)
                col4.metric("入场区间", f"{row['entry_low']} - {row['entry_high']}")
                col5.metric("止损", f"{row['stop_loss']}")
                col6.metric("目标", f"{row['target_1']} / {row['target_2']}")
                
                st.write(f"**逻辑标签**: {', '.join(row['logic_tags'] or [])}")
                st.write(f"**提及次数**: {row['mention_count']} | **来源数**: {row['source_diversity']}")
                
                sources = query_df("""
                    SELECT e.source_name, e.recommendation_type, 
                           e.strength, e.logic_summary, e.confidence, e.pub_time
                    FROM extracted_recommendations e
                    WHERE e.ts_code = %s AND e.pub_time >= %s
                    ORDER BY e.strength DESC;
                """, (row['ts_code'], selected_date - pd.Timedelta(days=2)))
                
                if not sources.empty:
                    st.write("**来源详情**:")
                    st.dataframe(sources, use_container_width=True)

elif page == "🔍 信号提取":
    st.header("LLM 信号提取结果")
    
    col1, col2 = st.columns(2)
    with col1:
        rec_type = st.multiselect(
            "推荐类型", 
            ["buy", "strong_buy", "watch", "sell", "neutral"],
            default=["buy", "strong_buy", "watch"]
        )
    with col2:
        min_strength = st.slider("最小强度", 1, 5, 2)
    
    df = query_df("""
        SELECT e.ts_code, e.stock_name, e.recommendation_type, e.strength,
               e.logic_category, e.logic_summary, e.confidence, e.pub_time,
               e.source_name, r.title AS article_title, r.url AS article_url
        FROM extracted_recommendations e
        LEFT JOIN raw_signals r ON e.raw_signal_id = r.id
        WHERE e.recommendation_type = ANY(%s) AND e.strength >= %s
        ORDER BY e.pub_time DESC
        LIMIT 200;
    """, (rec_type, min_strength))
    
    if df.empty:
        st.info("暂无数据")
    else:
        st.dataframe(df, use_container_width=True, height=700)
        
        st.subheader("原文链接")
        for _, row in df.iterrows():
            if row['article_url']:
                st.markdown(f"[{row['article_title'] or '无标题'}]({row['article_url']}) - {row['source_name']}")

elif page == "📡 数据源":
    st.header("数据源统计")
    
    stats = query_df("""
        SELECT source_name, COUNT(*) as signal_count, 
               AVG(confidence) as avg_confidence,
               AVG(strength) as avg_strength
        FROM extracted_recommendations
        GROUP BY source_name
        ORDER BY signal_count DESC;
    """)
    
    if not stats.empty:
        st.dataframe(stats, use_container_width=True)

elif page == "📰 原始资讯":
    st.header("原始资讯")
    
    sources = query_df("SELECT DISTINCT source_name FROM raw_signals ORDER BY source_name;")
    source_filter = st.selectbox("数据源", ["全部"] + sources['source_name'].tolist())
    
    if source_filter == "全部":
        df = query_df("""
            SELECT r.title, r.content, r.url, r.pub_time, r.fetch_time,
                   r.source_name
            FROM raw_signals r
            ORDER BY r.fetch_time DESC
            LIMIT 100;
        """)
    else:
        df = query_df("""
            SELECT r.title, r.content, r.url, r.pub_time, r.fetch_time,
                   r.source_name
            FROM raw_signals r
            WHERE r.source_name = %s
            ORDER BY r.fetch_time DESC
            LIMIT 100;
        """, (source_filter,))
    
    if not df.empty:
        for _, row in df.iterrows():
            with st.expander(f"{row['source_name']} | {row['title'] or '无标题'}"):
                st.write(f"**发布时间**: {row['pub_time']}")
                st.write(f"**采集时间**: {row['fetch_time']}")
                if row['url']:
                    st.markdown(f"[🔗 原文链接]({row['url']})")
                st.markdown("---")
                st.write(row['content'][:500] + "..." if row['content'] and len(row['content']) > 500 else row['content'])
