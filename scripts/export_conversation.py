# -*- coding: utf-8 -*-
"""导出当前对话历史全集与规划文档脚本。"""

import os
import json
import re
import shutil
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_BRAIN = r"C:\Users\xx\.gemini\antigravity\brain\c52941b6-f216-4224-82fb-db9136b01946"
LOGS_DIR = os.path.join(SOURCE_BRAIN, ".system_generated", "logs")
EXPORT_DIR = os.path.join(ROOT, "project_exports")

def export_all():
    os.makedirs(EXPORT_DIR, exist_ok=True)

    # 1. 复制规划与架构设计文档
    plans = ["implementation_plan.md", "walkthrough.md", "mobile_preview.html"]
    copied_plans = []
    for p in plans:
        src = os.path.join(SOURCE_BRAIN, p)
        dst = os.path.join(EXPORT_DIR, p)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            copied_plans.append(p)
    print(f"[1/4] 已成功导出规划与走查文档: {', '.join(copied_plans)}")

    # 2. 复制原始 jsonl 对话流
    transcripts = ["transcript_full.jsonl", "transcript.jsonl"]
    copied_transcripts = []
    for t in transcripts:
        src = os.path.join(LOGS_DIR, t)
        dst = os.path.join(EXPORT_DIR, t)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            copied_transcripts.append(t)
    print(f"[2/4] 已成功导出原始 JSONL 对话记录: {', '.join(copied_transcripts)}")

    # 3. 结构化解析生成人类易读的高清 Markdown 对话录
    readable_md_path = os.path.join(EXPORT_DIR, "conversation_history_readable.md")
    full_jsonl = os.path.join(LOGS_DIR, "transcript_full.jsonl")

    dialogue_turns = []
    current_user = None
    current_model_responses = []

    with open(full_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
            except Exception:
                continue

            stype = item.get("type")
            source = item.get("source")
            created_at = item.get("created_at", "")
            content = item.get("content", "")

            if source == "USER_EXPLICIT" and stype == "USER_INPUT":
                if current_user is not None:
                    dialogue_turns.append({
                        "user": current_user,
                        "model": current_model_responses,
                    })
                    current_model_responses = []

                # 提取纯用户提问文本
                m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
                clean_q = m.group(1).strip() if m else content.strip()
                clean_q_display = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", clean_q, flags=re.DOTALL).strip()
                clean_q_display = re.sub(r"<uploaded_files>.*?</uploaded_files>", "", clean_q_display, flags=re.DOTALL).strip()
                if not clean_q_display:
                    clean_q_display = "（用户上传操作截图 / 确认推进指令）"

                current_user = {
                    "time": created_at,
                    "content": clean_q_display,
                }

            elif source == "MODEL" and stype == "PLANNER_RESPONSE":
                if content and content.strip():
                    if "No tools were called" not in content and "Task logs are available at" not in content:
                        current_model_responses.append({
                            "time": created_at,
                            "content": content.strip(),
                        })

    if current_user is not None:
        dialogue_turns.append({
            "user": current_user,
            "model": current_model_responses,
        })

    with open(readable_md_path, "w", encoding="utf-8") as out:
        out.write("# 教师与学校数字化评估项目 · 全程对话与规划记录全集\n\n")
        out.write(f"> **导出时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
        out.write(f"> **会话 ID**：`c52941b6-f216-4224-82fb-db9136b01946`  \n")
        out.write(f"> **全历程交流轮次**：共 {len(dialogue_turns)} 轮  \n")
        out.write("> **包含内容**：需求分析、架构规划、Agent 工具调用、Python 绘图设计、全量排错与清理说明  \n\n")
        out.write("---\n\n")
        out.write("## 目录导航\n\n")
        for idx, turn in enumerate(dialogue_turns, 1):
            q_brief = turn["user"]["content"].replace("\n", " ")[:40]
            time_short = turn["user"]["time"][:19].replace("T", " ")
            out.write(f"- [第 {idx:02d} 轮 ({time_short})：{q_brief}](#第-{idx:02d}-轮)\n")
        out.write("\n---\n\n")

        for idx, turn in enumerate(dialogue_turns, 1):
            time_str = turn["user"]["time"][:19].replace("T", " ")
            out.write(f"## 第 {idx:02d} 轮\n\n")
            out.write(f"📅 **时间**：`{time_str}`\n\n")
            out.write(f"### 👤 用户需求 (User Request)\n\n")
            out.write(f"```text\n{turn['user']['content']}\n```\n\n")
            out.write(f"### 🤖 助手答复与方案执行 (Assistant Response)\n\n")
            if turn["model"]:
                for m_idx, m_resp in enumerate(turn["model"]):
                    if len(turn["model"]) > 1:
                        out.write(f"**[进度阶段 {m_idx + 1}]**\n\n")
                    out.write(f"{m_resp['content']}\n\n")
            else:
                out.write("*(本轮触发自动化背景工具执行并直接过渡至下一确认阶段)*\n\n")
            out.write("---\n\n")

    print(f"[3/4] 已生成人类可读的高清 Markdown 对话记录: conversation_history_readable.md ({len(dialogue_turns)} 轮)")

    # 4. 打包为 ZIP 便携压缩包
    zip_path = os.path.join(EXPORT_DIR, "TeacherEval_Conversation_and_Plans_Export.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(EXPORT_DIR):
            for file in files:
                if file.endswith(".zip"):
                    continue
                fp = os.path.join(root, file)
                rel_name = os.path.relpath(fp, EXPORT_DIR)
                zf.write(fp, arcname=rel_name)

    size_mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"[4/4] 已生成便携式压缩包: TeacherEval_Conversation_and_Plans_Export.zip ({size_mb:.2f} MB)")
    print("\n所有导出文件均已保存在: d:\\teacher evaluation\\project_exports\\")

if __name__ == "__main__":
    export_all()
