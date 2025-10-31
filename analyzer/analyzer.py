import streamlit as st
import pandas as pd
import re
import json
import plotly.express as px
import os
from collections import defaultdict

# Streamlitのキャッシュ機能で、ファイル読み込みと解析を高速化します
@st.cache_data(show_spinner="ログファイルを解析中...")
def load_log_data(log_file):
    """
    指定されたログファイルを解析し、ハンドごとの行動データをDataFrameとして返します。
    """
    if not os.path.exists(log_file):
        # main関数側でst.errorを表示するため、ここでは空のDFを返す
        return pd.DataFrame()

    data = []
    current_hand = 0
    agent_prompts = {}
    lines = []
    line_count = 0
    i = 0 
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        line_count = len(lines)

        while i < line_count:
            line = lines[i]

            # 1. 新しいハンド（ゲーム）の開始を検出
            hand_match = re.search(r"=== STARTING NEW HAND #(\d+) ===", line)
            if hand_match:
                current_hand = int(hand_match.group(1))
                agent_prompts = {} # ハンドが変わったらプロンプト情報をリセット
                i += 1
                continue

            # 2. 'LLM Prompt for AgentX: {' という行を探す
            prompt_match = re.search(r"LLM Prompt for (Agent\d+): \{", line)
            if prompt_match:
                agent_name = prompt_match.group(1)
                json_str = "{\n" # '{' はもう見つけた
                json_start_line = i + 1
                i += 1 # 次の行から読み込み開始
                bracket_level = 1
                
                # JSONブロック（'{'から'}'まで）を読み込む
                while i < line_count:
                    json_line = lines[i]
                    
                    brace_close_count = json_line.count("}")
                    if brace_close_count > 0:
                        bracket_level -= brace_close_count
                        
                        if bracket_level == 0:
                            # この行でJSONが終わる
                            last_brace_index = json_line.rfind('}')
                            json_str += json_line[:last_brace_index + 1]
                            break # JSON読み取りを終了
                        elif bracket_level < 0:
                            # 括弧の対応が取れない (ログが壊れている可能性)
                            json_str += json_line
                            break
                        else:
                            json_str += json_line
                            
                    elif "{" in json_line:
                        bracket_level += json_line.count("{")
                        json_str += json_line
                    else:
                        json_str += json_line
                        
                    i += 1
                
                if bracket_level != 0:
                    # JSONが途中で切れている
                    i += 1
                    continue
                    
                try:
                    prompt_data = json.loads(json_str)
                    agent_prompts[agent_name] = (prompt_data, json_str)
                except json.JSONDecodeError:
                    # パース失敗時は、古い情報が残らないようにキーを削除する
                    agent_prompts.pop(agent_name, None)
                    pass
                
                i += 1
                continue

            # 3. エージェントの出力情報（Decision）を検出
            decision_match = re.search(r"\[(Agent\d+)\] Successfully parsed decision: (\w+), (\d+), (.*)", line)
            if decision_match:
                agent_name = decision_match.group(1)
                action = decision_match.group(2)
                amount = int(decision_match.group(3))
                reasoning = decision_match.group(4).strip()

                if agent_name in agent_prompts:
                    prompt_data, prompt_str = agent_prompts[agent_name]
                    
                    # コミュニティカードの枚数からフェーズを判定
                    community_cards_list = prompt_data.get("community", [])
                    num_community_cards = len(community_cards_list)
                    
                    if num_community_cards == 0:
                        derived_phase = "preflop"
                    elif num_community_cards == 3:
                        derived_phase = "flop"
                    elif num_community_cards == 4:
                        derived_phase = "turn"
                    elif num_community_cards == 5:
                        derived_phase = "river"
                    else:
                        derived_phase = "unknown"
                    
                    data.append({
                        "hand_id": current_hand,
                        "agent_name": agent_name,
                        "phase": derived_phase,
                        "your_chips_before": prompt_data.get("your_chips", 0),
                        "your_cards": ", ".join(prompt_data.get("your_cards", [])),
                        "community_cards": ", ".join(community_cards_list),
                        "action": action,
                        "amount": amount,
                        "reasoning": reasoning,
                        "input_prompt_json": prompt_data,
                    })

                else:
                    # 対応するプロンプトが見つからない (ログ前半など)
                    pass
                
                i += 1
                continue
                
            i += 1 # マッチしない行は次の行へ

    except Exception as e:
        # 解析中に予期せぬエラーが発生した場合
        st.error(f"ログ解析中に予期せぬエラーが発生しました: {e}")
        return pd.DataFrame()

    return pd.DataFrame(data)

# Streamlitのメインアプリケーション部分
def main():
    st.set_page_config(layout="wide")
    st.title("🃏 ポーカーAIログ分析ツール")

    LOG_FILE = "poker_game_20251031_184427_838e.log"
    
    if not os.path.exists(LOG_FILE):
        st.error(f"エラー: ログファイル '{LOG_FILE}' が見つかりません。")
        st.info(f"スクリプト (`poker_analyzer.py`) と同じディレクトリに '{LOG_FILE}' を配置してください。")
        st.stop()

    df = load_log_data(LOG_FILE)

    if df.empty:
        st.warning("ログデータが読み込めませんでした (0件のデータを検出)。")
        st.info("ログファイルの内容や、スクリプトの解析ロジックを確認してください。")
        return

    st.success(f"'{LOG_FILE}' から {len(df)} 件の行動データを正常に読み込みました。")
    
    # --- サイドバー (フィルタ) ---
    st.sidebar.header("🔍 表示フィルタ")
    
    all_players = sorted(df['agent_name'].unique())
    all_hands = sorted(df['hand_id'].unique())
    
    # データを元にフェーズリストを動的生成
    poker_phase_order = {"preflop": 0, "flop": 1, "turn": 2, "river": 3}
    unique_phases = df['phase'].unique()
    # 存在するフェーズをポーカーの順序でソート (unknown等は最後尾=99)
    all_phases = sorted(unique_phases, key=lambda p: poker_phase_order.get(p, 99))

    selected_players = st.sidebar.multiselect(
        "プレイヤーを選択:",
        options=all_players,
        default=all_players
    )
    
    selected_hands = st.sidebar.multiselect(
        "ハンドIDを選択 (空の場合はすべて):",
        options=all_hands,
        default=[]
    )
    
    selected_phases = st.sidebar.multiselect(
        "フェーズを選択:",
        options=all_phases,
        default=all_phases
    )

    # --- フィルタを適用したDataFrameを作成 ---
    query_parts = []
    
    query_parts.append("`agent_name` in @selected_players")
    query_parts.append("`phase` in @selected_phases")
    
    if selected_hands:
        query_parts.append("`hand_id` in @selected_hands")
        
    query_str = " & ".join(query_parts)
    filtered_df = df.query(query_str).copy()
    
    
    if filtered_df.empty:
        st.warning("フィルタに一致するデータがありません。")
        if not selected_players:
            st.info("サイドバーで「プレイヤー」を1人以上選択してください。")
        if not selected_phases:
            st.info("サイドバーで「フェーズ」を1つ以上選択してください。")
    else:
        # --- MUST 1: ハンドごとの行動詳細 ---
        st.header("MUST 1: ハンドごとの行動詳細 (フィルタ適用)")
        st.info(f"現在 {len(filtered_df)} 件の行動が表示されています。")
        display_cols = [
            "hand_id", "agent_name", "phase", 
            "action", "amount", "reasoning", 
            "your_cards", "community_cards"
        ]
        st.dataframe(filtered_df[display_cols])

        # --- MUST 2: ゲーム全体の行動分析 ---
        st.header("MUST 2: 選択範囲全体の行動分析 (フィルタ適用)")
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("アクション頻度 (円グラフ)")
            action_counts = filtered_df['action'].value_counts().reset_index()
            action_counts.columns = ['action', 'count']
            fig_action_pie = px.pie(action_counts, 
                                    names='action', 
                                    values='count',
                                    title="アクションの全体比率",
                                    hole=0.3)
            fig_action_pie.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig_action_pie, use_container_width=True)

        with col2:
            st.subheader("アクション頻度 (プレイヤー別)")
            player_action_counts = filtered_df.groupby('agent_name')['action'].value_counts().rename('count').reset_index()
            fig_action_bar = px.bar(player_action_counts, 
                                     x='agent_name', 
                                     y='count',
                                     color='action',
                                     title="プレイヤー別のアクション回数",
                                     labels={'agent_name': 'プレイヤー', 'count': '回数', 'action': 'アクション'})
            st.plotly_chart(fig_action_bar, use_container_width=True)

        st.subheader("ベット/レイズ額の分布")
        bet_amounts_df = filtered_df[filtered_df['amount'] > 0]
        if not bet_amounts_df.empty:
            fig_amount_hist = px.histogram(bet_amounts_df, 
                                             x="amount",
                                             color="agent_name",
                                             marginal="box",
                                             title="ベット/レイズ額の分布 (プレイヤー別)",
                                             labels={'amount': '金額', 'agent_name': 'プレイヤー'})
            st.plotly_chart(fig_amount_hist, use_container_width=True)
        else:
            st.info("選択範囲にベット/レイズのアクションがありません。")

        st.subheader("思考理由 (Reasoning) のサンプル")
        sample_size = min(10, len(filtered_df))
        if sample_size > 0:
            st.dataframe(filtered_df[['hand_id', 'agent_name', 'phase', 'action', 'reasoning']].sample(sample_size))
        else:
            st.info("サンプリングするデータがありません。")

        # --- MUST 3: 入力情報と出力情報の関係 ---
        st.header("MUST 3: 入力情報と出力情報の関係")
        st.info("上の「ハンドごとの行動詳細」テーブルで詳細を確認したい行のインデックス（一番左の数字）を選んでください。")
        
        available_indices = filtered_df.index.tolist()
        
        if available_indices:
            # 選択肢が多すぎると重くなるため、最大1000件に制限
            if len(available_indices) > 1000:
                st.info("表示件数が多すぎるため、インデックス選択は先頭1000件に制限されます。")
                available_indices = available_indices[:1000]

            selected_index = st.selectbox(
                "分析したいターンのインデックスを選択:",
                options=available_indices,
                format_func=lambda x: f"Index {x} (Hand {df.loc[x]['hand_id']}, {df.loc[x]['agent_name']}, {df.loc[x]['phase']})"
            )

            if selected_index is not None and selected_index in df.index:
                selected_row = df.loc[selected_index]
                
                st.subheader(f"Index {selected_index} の詳細")
                
                in_col, out_col = st.columns(2)
                
                with in_col:
                    st.markdown("#### 📥 入力情報 (Input Prompt)")
                    st.json(selected_row['input_prompt_json'])
                    
                with out_col:
                    st.markdown("#### 📤 出力情報 (Decision)")
                    st.metric("プレイヤー", selected_row['agent_name'])
                    st.metric("アクション", selected_row['action'])
                    st.metric("金額", f"{selected_row['amount']:,}")
                    st.markdown("**思考理由:**")
                    st.info(selected_row['reasoning'])
            elif selected_index is not None:
                st.warning(f"選択されたインデックス {selected_index} は有効ではありません。")
        else:
            st.info("表示するデータがありません。")

        # --- ADDITIONAL: 追加分析 ---
        st.header("ADDITIONAL: 追加分析 (フィルタ適用)")
        col_add1, col_add2 = st.columns(2)
        
        with col_add1:
            st.subheader("フェーズごとのアクション比率")
            phase_action_counts = filtered_df.groupby(['phase', 'action']).size().reset_index(name='count')
            if not phase_action_counts.empty:
                # フェーズの順序を適用
                phase_action_counts['phase'] = pd.Categorical(phase_action_counts['phase'], categories=all_phases, ordered=True)
                fig_phase_action = px.bar(
                    phase_action_counts,
                    x='phase',
                    y='count',
                    color='action',
                    title="フェーズごとのアクション内訳",
                    labels={'phase': 'フェーズ', 'count': '回数', 'action': 'アクション'},
                )
                # X軸のソートを無効にし、カテゴリカルの順序（poker_phase_order）を優先
                fig_phase_action.update_xaxes(categoryorder=None) 
                st.plotly_chart(fig_phase_action, use_container_width=True)
            else:
                st.info("データがありません。")
        
        with col_add2:
            st.subheader("チップ量の推移 (ハンド開始時点)")
            # フィルタされたプレイヤー（selected_players）の
            # チップ推移（全ハンド分）を表示する
            chips_df = df[df['agent_name'].isin(selected_players)]
            
            # 各ハンドIDの最初のレコード（そのハンドの開始時チップ）を取得
            chips_over_time = chips_df.groupby(['agent_name', 'hand_id'])['your_chips_before'].first().reset_index()
            
            if not chips_over_time.empty:
                fig_chips = px.line(
                    chips_over_time,
                    x='hand_id',
                    y='your_chips_before',
                    color='agent_name',
                    title="ハンド開始時のチップ量推移",
                    labels={'hand_id': 'ハンドID', 'your_chips_before': 'チップ量', 'agent_name': 'プレイヤー'},
                    markers=True
                )
                st.plotly_chart(fig_chips, use_container_width=True)
            else:
                st.info("チップデータがありません。")

if __name__ == "__main__":
    main()