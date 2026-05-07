"""AI Stock Recommendation - Web UI"""
import os
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import streamlit as st
from datetime import date, datetime
import json

st.set_page_config(page_title="AI 股票推荐系统", layout="wide", page_icon="📊")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_db_config():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(BASE_DIR, '.env'))
    except:
        pass
    return {
        'host': os.getenv('POSTGRES_HOST', 'localhost'),
        'port': int(os.getenv('POSTGRES_PORT', 5432)),
        'user': os.getenv('POSTGRES_USER', 'stockrec'),
        'password': os.getenv('POSTGRES_PASSWORD', ''),
        'dbname': os.getenv('POSTGRES_DB', 'stockrec_db'),
    }


@st.cache_data(ttl=60)
def query_df(sql, params=None):
    cfg = get_db_config()
    conn = psycopg2.connect(**cfg)
    df = pd.read_sql(sql, conn, params=params)
    conn.close()
    return df


def get_db():
    cfg = get_db_config()
    return psycopg2.connect(**cfg)


st.sidebar.title("📊 AI 股票推荐")
st.sidebar.markdown("---")
page = st.sidebar.radio("导航", ["📈 今日候选", "🔍 信号提取", "📡 数据源", "📰 原始资讯"])

st.title("AI 股票推荐系统")

if page == "📈 今日候选":
    st.header("今日候选股")
    
    selected_date = st.date_input("选择日期", value=date.today())
    
    df = query_df("""
        SELECT ts_code, stock_name, final_score, llm_score, quant_score, 
               consensus_score, mention_count, source_diversity,
               selected, position_pct, entry_low, entry_high, stop_loss, 
               target_1, target_2, logic_tags
        FROM daily_candidates 
        WHERE snapshot_date = %s 
        ORDER BY final_score DESC;
    """, (selected_date,))
    
    if df.empty:
        st.info(f"{selected_date} 暂无候选数据")
    else:
        def highlight_selected(row):
            if row['selected']:
                return ['background-color: #d4edda'] * len(row)
            return [''] * len(row)
        
        st.dataframe(
            df.style.apply(highlight_selected, axis=1),
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
