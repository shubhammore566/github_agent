"""
GitHub Agent - simple Streamlit app

Run:
    pip install -r requirements.txt
    streamlit run app.py

The app keeps API keys and the GitHub token only in Streamlit session state.
It never writes them to a file.
"""

import io
import json
import html
import hashlib
import os
import re
import tempfile
import time
import zipfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

try:
    # Auto-load a local .env file (if present) so GITHUB_CLIENT_ID,
    # GITHUB_CLIENT_SECRET and PUBLIC_APP_URL work just by creating a .env
    # file next to app.py - no need to set OS environment variables by hand.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import streamlit as st
import streamlit.components.v1 as components
from github import Github, GithubException
from github_agent.aws_oidc import (
    WORKFLOW_PATH as AWS_OIDC_WORKFLOW_PATH,
    cloudformation_template as aws_cloudformation_template,
    dispatch_and_wait as aws_oidc_dispatch_and_wait,
    ensure_workflow as ensure_aws_oidc_workflow,
    quick_create_url as aws_quick_create_url,
    valid_role_arn as valid_aws_role_arn,
    role_arn_from_input as aws_role_arn_from_input,
)

# Optional one-time developer setup: host the (non-secret) CloudFormation
# template in a public-read S3 object once, then set this env var to its
# https URL. When set, the sidebar's "Connect my AWS account" button becomes
# a direct AWS Console deep link (sign in -> land straight on a pre-filled
# Create stack page for the visitor's own account) instead of a manual
# download-then-upload flow. AWS's quick-create links only work with an
# S3-hosted template - see README "One-time AWS setup (developer)".
_ENV_AWS_OIDC_TEMPLATE_URL = os.getenv("AWS_OIDC_TEMPLATE_URL", "").strip()
_ENV_AWS_SETUP_CALLBACK_URL = os.getenv("AWS_SETUP_CALLBACK_URL", "").strip().rstrip("/")




st.set_page_config(page_title="GitHub Agent", page_icon="🤖", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

:root{
  --bg-0:#f2f0ff; --bg-1:#ffffff; --bg-2:#f6f4ff; --surface:#ffffff;
  --border:rgba(124,92,255,.22); --border-soft:rgba(90,70,180,.14);
  --violet:#7c5cff; --violet-2:#5b3df0; --teal:#0fb8a6; --teal-2:#0a8f81;
  --pink:#ff5da2; --orange:#ff9457; --amber:#ffb020;
  --text-0:#241f3d; --text-1:#4c4670; --text-2:#7a7398;
  --grad-brand:linear-gradient(135deg,#7c5cff 0%,#ff5da2 55%,#0fb8a6 100%);
  --grad-brand-soft:linear-gradient(135deg,rgba(124,92,255,.16),rgba(255,93,162,.14),rgba(15,184,166,.14));
  --radius-lg:20px; --radius-md:14px; --radius-sm:10px;
}

html, body, [class*="css"]{font-family:'Plus Jakarta Sans',-apple-system,Segoe UI,sans-serif !important;}
.stApp{background:
  radial-gradient(1000px 560px at 8% -10%, rgba(124,92,255,.18), transparent 60%),
  radial-gradient(900px 520px at 100% 0%, rgba(15,184,166,.16), transparent 55%),
  radial-gradient(800px 480px at 60% 110%, rgba(255,93,162,.14), transparent 55%),
  var(--bg-0) !important;}

/* ---------- Hero ---------- */
.main-hero{position:relative;padding:30px 34px;border-radius:var(--radius-lg);margin-bottom:22px;
  background:var(--grad-brand);
  border:1px solid rgba(255,255,255,.35);overflow:hidden;
  box-shadow:0 24px 60px -22px rgba(124,92,255,.55);}
.main-hero::before{content:"";position:absolute;inset:0;
  background:radial-gradient(460px 240px at 92% -25%, rgba(255,255,255,.35), transparent 70%);pointer-events:none;}
.main-hero h1{margin:0 0 8px 0;font-size:36px;font-weight:800;letter-spacing:-.5px;position:relative;
  color:#ffffff;text-shadow:0 2px 18px rgba(0,0,0,.12);}
.main-hero p{margin:0 0 16px 0;color:rgba(255,255,255,.92);font-size:15.5px;max-width:660px;position:relative;}
.hero-chips{display:flex;flex-wrap:wrap;gap:8px;position:relative;}
.hero-chip{font-size:12.5px;font-weight:700;padding:7px 14px;border-radius:999px;
  background:rgba(255,255,255,.22);border:1px solid rgba(255,255,255,.4);color:#ffffff;
  display:inline-flex;align-items:center;gap:6px;backdrop-filter:blur(4px);
  transition:transform .15s ease, background .15s ease;}
.hero-chip:hover{transform:translateY(-2px) scale(1.04);background:rgba(255,255,255,.32);}
.hero-chip.arrow{background:transparent;border:none;color:rgba(255,255,255,.85);padding:7px 0;}

/* ---------- Sidebar ---------- */
[data-testid="stSidebar"]{background:linear-gradient(180deg,#ffffff,#f5f2ff) !important;
  border-right:1px solid var(--border-soft);}
[data-testid="stSidebar"] h2{font-size:14.5px !important;font-weight:800 !important;
  letter-spacing:.2px;color:var(--violet-2) !important;
  padding-bottom:8px;border-bottom:1px solid var(--border-soft);margin-bottom:10px !important;}
[data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small{color:var(--text-2) !important;}

/* ---------- Generic text ---------- */
h1,h2,h3,h4,h5{color:var(--text-0) !important;}
p, .stMarkdown, label, span{color:var(--text-1);}
hr{border-color:var(--border-soft) !important;margin:18px 0 !important;}

/* ---------- Inputs ---------- */
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input, .stSelectbox [data-baseweb="select"]>div{
  background:var(--surface) !important;border:1px solid var(--border-soft) !important;
  border-radius:var(--radius-sm) !important;color:var(--text-0) !important;
  transition:border-color .15s ease, box-shadow .15s ease;}
[data-testid="stTextInput"] input:focus, [data-testid="stTextArea"] textarea:focus{
  border-color:var(--violet) !important;box-shadow:0 0 0 3px rgba(124,92,255,.18) !important;}

/* ---------- Buttons ---------- */
.stButton>button, .stLinkButton>a, .stDownloadButton>button{
  border-radius:999px !important;border:1px solid var(--border) !important;
  background:var(--surface) !important;color:var(--violet-2) !important;font-weight:700 !important;
  padding:.5rem 1.1rem !important;transition:all .18s ease !important;
  box-shadow:0 4px 14px -8px rgba(124,92,255,.35) !important;}
.stButton>button:hover, .stLinkButton>a:hover, .stDownloadButton>button:hover{
  border-color:var(--violet) !important;transform:translateY(-2px) scale(1.015);
  box-shadow:0 12px 28px -10px rgba(124,92,255,.55) !important;
  background:linear-gradient(135deg,rgba(124,92,255,.10),rgba(255,93,162,.10)) !important;}
.stButton>button[kind="primary"], button[data-testid="baseButton-primary"]{
  background:var(--grad-brand) !important;border:none !important;color:#ffffff !important;
  box-shadow:0 12px 30px -12px rgba(124,92,255,.65) !important;}
.stButton>button[kind="primary"]:hover, button[data-testid="baseButton-primary"]:hover{
  filter:brightness(1.08);transform:translateY(-2px) scale(1.02);}

/* ---------- Tabs -> segmented pill nav ---------- */
[data-testid="stTabs"] [data-baseweb="tab-list"]{gap:4px;background:var(--surface);
  padding:4px;border-radius:999px;border:1px solid var(--border-soft);
  box-shadow:0 4px 16px -12px rgba(124,92,255,.3);}
[data-testid="stTabs"] button[data-baseweb="tab"]{border-radius:999px !important;
  color:var(--text-2) !important;font-weight:700 !important;transition:all .15s ease !important;}
[data-testid="stTabs"] button[data-baseweb="tab"]:hover{color:var(--violet) !important;}
[data-testid="stTabs"] button[aria-selected="true"]{background:var(--grad-brand) !important;
  color:#ffffff !important;}
[data-testid="stTabs"] [data-baseweb="tab-highlight"]{display:none;}
[data-testid="stTabs"] [data-baseweb="tab-border"]{display:none;}

/* ---------- Expander ---------- */
[data-testid="stExpander"]{background:var(--surface) !important;border:1px solid var(--border-soft) !important;
  border-radius:var(--radius-md) !important;overflow:hidden;
  box-shadow:0 8px 24px -18px rgba(124,92,255,.4);transition:box-shadow .15s ease;}
[data-testid="stExpander"]:hover{box-shadow:0 10px 28px -14px rgba(124,92,255,.5);}

/* ---------- Alerts ---------- */
[data-testid="stAlert"]{border-radius:var(--radius-md) !important;border:1px solid var(--border-soft) !important;}

/* ---------- Metrics ---------- */
[data-testid="stMetric"]{background:linear-gradient(135deg,#ffffff,#f7f4ff);border:1px solid var(--border-soft);
  border-radius:var(--radius-md);padding:14px 16px;
  box-shadow:0 12px 30px -20px rgba(124,92,255,.4);transition:transform .15s ease, box-shadow .15s ease;
  border-left:3px solid var(--violet);}
[data-testid="stMetric"]:hover{transform:translateY(-2px);box-shadow:0 16px 34px -18px rgba(124,92,255,.55);}
[data-testid="stMetricLabel"]{color:var(--text-2) !important;}
[data-testid="stMetricValue"]{color:var(--violet-2) !important;font-weight:800 !important;}

/* ---------- Chat ---------- */
[data-testid="stChatMessage"]{background:var(--surface);border:1px solid var(--border-soft);
  border-radius:var(--radius-md);padding:6px 10px;margin-bottom:8px;
  box-shadow:0 8px 22px -18px rgba(124,92,255,.35);
  border-left:4px solid var(--teal);transition:transform .12s ease;}
[data-testid="stChatMessage"]:hover{transform:translateX(2px);}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]){border-left-color:var(--pink);}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]){border-left-color:var(--violet);}
[data-testid="stChatInput"]{
  border-radius:999px !important;
  border:1px solid rgba(124,92,255,.32) !important;
  background:var(--surface) !important;
  box-shadow:0 8px 24px -16px rgba(124,92,255,.4) !important;
}
[data-testid="stChatInput"] textarea,
[data-testid="stChatInput"] input,
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] div[contenteditable],
[data-testid="stChatInput"] [class*="Input"]{
  background:var(--surface) !important;
  border:none !important;
  border-bottom:none !important;
  box-shadow:none !important;
  outline:none !important;
  color:var(--text-0) !important;
}
/* Kill any red / error / dotted underline on chat input */
[data-testid="stChatInput"] *,
[data-testid="stChatInput"]::before,
[data-testid="stChatInput"]::after,
div[data-testid="stChatInputContainer"] *,
div[data-testid="stBottomBlockContainer"] [data-testid="stChatInput"] *{
  border-color:rgba(124,92,255,.32) !important;
  outline-color:transparent !important;
}
/* Remove red focus / invalid states */
[data-testid="stChatInput"]:focus-within,
[data-testid="stChatInput"]:focus,
[data-testid="stChatInput"] textarea:focus,
[data-testid="stChatInput"] input:focus{
  border:1px solid rgba(124,92,255,.6) !important;
  box-shadow:0 0 0 3px rgba(124,92,255,.18) !important;
  outline:none !important;
}
/* Mic button — make it obviously colorful and give clear hover/active states
   instead of relying on faded default styling that reads as "broken" */
[data-testid="stChatInput"] button{
  transition:transform .15s ease, box-shadow .15s ease !important;
  border-radius:999px !important;
}
[data-testid="stChatInput"] button:hover{
  transform:scale(1.08);
  box-shadow:0 0 0 6px rgba(124,92,255,.14) !important;
}
/* Audio recording indicator – vivid pink/teal instead of red or grey */
[data-testid="stChatInput"] [class*="recording"],
[data-testid="stChatInput"] [class*="Recording"],
[data-testid="stChatInput"] svg[stroke*="red"],
[data-testid="stChatInput"] [style*="red"],
[data-testid="stChatInput"] [style*="rgb(255"],
[data-testid="stChatInput"] [style*="#f"],
[data-testid="stChatInput"] [style*="#e"]{
  color:#ff5da2 !important;
  stroke:#ff5da2 !important;
  border-color:#ff5da2 !important;
  background:transparent !important;
  animation:nova-pulse 1.4s ease-in-out infinite;
}
/* Dotted red line / waveform kill */
[data-testid="stChatInput"] hr,
[data-testid="stChatInput"] [class*="divider"],
[data-testid="stChatInput"] [class*="underline"]{
  display:none !important;
  border:none !important;
  background:none !important;
}

/* ---------- Progress ---------- */
[data-testid="stProgress"] > div > div{background:var(--grad-brand) !important;}

/* ---------- Scrollbar ---------- */
::-webkit-scrollbar{width:9px;height:9px;}
::-webkit-scrollbar-thumb{background:rgba(124,92,255,.35);border-radius:8px;}
::-webkit-scrollbar-track{background:transparent;}

/* ---------- Force kill red / dotted error lines everywhere in chat area ---------- */
[data-testid="stBottomBlockContainer"] *,
[data-testid="stChatInput"] *,
section[data-testid="stChatInput"] *,
div[data-baseweb="input"] *,
div[data-baseweb="base-input"] * {
  border-bottom-color: transparent !important;
}
/* The native Streamlit chat input is kept mounted (it's still the real
   data pipe our custom Nova chat box talks to) but is no longer shown —
   the dark "Live AI Nova Assistant" box below fully replaces it visually. */
[data-testid="stChatInput"] {
  position: absolute !important;
  height: 0 !important;
  min-height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
  overflow: hidden !important;
  opacity: 0 !important;
  pointer-events: none !important;
  border: none !important;
  box-shadow: none !important;
}
/* When recording – colorful pulse instead of red */
@keyframes nova-pulse {
  0%,100% { box-shadow: 0 0 0 0 rgba(255,93,162,.45); }
  50% { box-shadow: 0 0 0 8px rgba(255,93,162,0); }
}

</style>
<div class="main-hero">
  <h1>🤖 GitHub Agent</h1>
  <p>Approval-gated automation that ships your project end-to-end — describe it in plain English, Hindi, or Hinglish, and watch it move from code to a live URL.</p>
  <div class="hero-chips">
    <span class="hero-chip">📦 Build</span><span class="hero-chip arrow">→</span>
    <span class="hero-chip">🐙 GitHub</span><span class="hero-chip arrow">→</span>
    <span class="hero-chip">🐳 Docker</span><span class="hero-chip arrow">→</span>
    <span class="hero-chip">☁️ AWS EC2</span><span class="hero-chip arrow">→</span>
    <span class="hero-chip">🔗 Live URL</span>
  </div>
</div>
""", unsafe_allow_html=True)



PROVIDERS = {
    "OpenAI": {
        "kind": "openai",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        # "chat-latest" is OpenAI's own rolling alias for its current default
        # chat model, so this default doesn't go stale when OpenAI ships a
        # new generation.
        "model": "chat-latest",
        "help": "OpenAI API key",
    },
    "Mistral": {
        "kind": "openai",
        "endpoint": "https://api.mistral.ai/v1/chat/completions",
        "model": "mistral-small-latest",
        "help": "Mistral API key",
    },
    "Anthropic": {
        "kind": "anthropic",
        "model": "claude-sonnet-5",
        "help": "Anthropic API key",
    },
    "Google Gemini": {
        "kind": "gemini",
        "model": "gemini-3.6-flash",
        "help": "Google AI Studio API key",
    },
    "Groq": {
        "kind": "openai",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        # Groq retired the Llama 3.x text models; gpt-oss-20b is their
        # current recommended fast/cheap replacement.
        "model": "openai/gpt-oss-20b",
        "help": "Groq API key",
    },
    "QwenCloud (Qwen / DeepSeek)": {
        "kind": "openai",
        "endpoint": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        # Default to a Qwen model since its free quota is shared across the
        # whole account; DeepSeek models on QwenCloud each have their own
        # separate free-quota bucket that can run out independently (change
        # the model field to e.g. "deepseek-v4-flash" if you prefer that).
        "model": "qwen3.8-flash",
        "help": "QwenCloud API key (from the dashboard's API Keys page, not the model page)",
    },
    "OpenRouter": {
        "kind": "openai",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "model": "openai/gpt-chat-latest",
        "help": "OpenRouter API key",
    },
    "Custom OpenAI-compatible": {
        "kind": "custom",
        "model": "gpt-4o-mini",
        "help": "Any OpenAI-compatible endpoint",
    },
}

DEPLOYMENT_PLATFORM_OPTIONS = (
    "Streamlit Cloud",
    "AWS EC2",
    "Azure",
    "Google Cloud",
    "Render",
)
ASK_DEPLOYMENT_PLATFORM = "Ask me on deploy"
DEPLOYMENT_PLATFORM_ALIASES = {
    "streamlit cloud": "Streamlit Cloud",
    "streamlit": "Streamlit Cloud",
    "streamlit community cloud": "Streamlit Cloud",
    "aws": "AWS EC2",
    "amazon web services": "AWS EC2",
    "aws ec2": "AWS EC2",
    "ec2": "AWS EC2",
    "amazon ec2": "AWS EC2",
    "azure": "Azure",
    "microsoft azure": "Azure",
    "google cloud": "Google Cloud",
    "gcp": "Google Cloud",
    "google cloud platform": "Google Cloud",
    "render": "Render",
}

# Models we should never auto-pick as a fallback chat model (image/audio/
# embedding/moderation models return valid /models entries but can't do
# chat completions).
NON_CHAT_MODEL_KEYWORDS = (
    "embed",
    "embedding",
    "tts",
    "audio",
    "speech",
    "whisper",
    "image",
    "vision-only",
    "moderation",
    "dall-e",
    "video",
    "veo",
    "imagen",
    "rerank",
)

# When auto-picking a replacement model, prefer small/fast/cheap ones first
# so recovery doesn't silently switch someone onto an expensive model.
PREFERRED_FALLBACK_KEYWORDS = (
    "flash",
    "mini",
    "haiku",
    "small",
    "8b",
    "20b",
    "instant",
    "lite",
    "chat-latest",
    "sonnet",
)

SYSTEM_PROMPT = """
You are a friendly GitHub Agent inside a Streamlit app.

The user may speak in English, Hindi, Hinglish, or another language. Reply in
the same language and style. Understand natural language; do not require
fixed commands, exact keywords, or a rigid syntax — infer intent the way a
sharp human assistant would, including from typos, shorthand, or casual
phrasing (e.g. "deploye kr", "isko github pe daal do", "ye wala push kr do").

Conversation and voice style rules:
- Sound like a calm, friendly human teammate, not a robot or a documentation reader.
- Keep normal replies short: usually one or two natural sentences.
- If a tool returns many repositories, files, issues, or actions, do NOT read every item
  in the reply. Give a compact summary and ask the user what they want next.
  Example: "Shubham, tumhari GitHub repo list open hai. Ek baar check karo aur batao
  kaunsa project deploy karna hai."
- Use conversational pauses through punctuation, but never add stage directions or
  fake audio cues. Avoid long numbered lists unless the user explicitly asks for them.
- The reply will be spoken aloud, so prefer simple sentences, natural wording, and
  easy-to-pronounce English/Hindi/Hinglish.

You will also receive a separate system message titled "Current app state"
before each request. Always read it and use it to fill in details the user
left implicit — for example if they say "deploy kar do" and the state shows
a recently pushed repository or an already-open repository, use that
repository instead of asking the user to repeat it. Only ask a clarifying
question when the state truly gives no reasonable way to infer what is
meant (e.g. multiple plausible repos and none recently used, or a
genuinely destructive action with real ambiguity about the target).

Return ONLY valid JSON, with this exact shape:
{
  "reply": "short helpful response in the user's language",
  "action": null
}

For a GitHub action, action must be an object with one of these types:
- {"type":"list_repos"}
- {"type":"analyze_project"}
- {"type":"open_repo","repo":"owner/repo"}
- {"type":"create_repo","name":"...","private":false,"description":"..."}
- {"type":"create_repo","name":"...","owner":"optional-org","private":false,"description":"..."}
- {"type":"rename_repo","repo":"owner/repo","new_name":"..."}
- {"type":"create_issue","repo":"owner/repo","title":"...","body":"..."}
- {"type":"list_issues","repo":"owner/repo"}
- {"type":"update_issue","repo":"owner/repo","number":1,"title":"optional","body":"optional","state":"open"}
- {"type":"close_pull_request","repo":"owner/repo","number":1}
- {"type":"read_file","repo":"owner/repo","path":"path/to/file"}
- {"type":"update_file","repo":"owner/repo","path":"...","content":"..."}
- {"type":"delete_file","repo":"owner/repo","path":"..."}
- {"type":"create_branch","repo":"owner/repo","branch":"...","from_branch":"main"}
- {"type":"create_pull_request","repo":"owner/repo","title":"...","head":"...","base":"main","body":"..."}
- {"type":"merge_pull_request","repo":"owner/repo","number":1,"commit_message":"..."}
- {"type":"close_issue","repo":"owner/repo","number":1}
- {"type":"delete_repo","repo":"owner/repo"}
- {"type":"list_releases","repo":"owner/repo"}
- {"type":"create_release","repo":"owner/repo","tag":"v1.0.0","name":"...","body":"..."}
- {"type":"delete_release","repo":"owner/repo","release_id":1}
- {"type":"list_workflows","repo":"owner/repo"}
- {"type":"repo_settings","repo":"owner/repo"}
- {"type":"update_repo_settings","repo":"owner/repo","changes":{"private":true,"description":"...","default_branch":"...","has_issues":true,"has_wiki":true,"has_projects":true,"delete_branch_on_merge":true,"topics":["..."]}}
- {"type":"run_workflow","repo":"owner/repo","workflow":"ci.yml","ref":"main","inputs":{}}
- {"type":"deploy_streamlit","repo":"owner/repo","branch":"main","app_path":"app.py"}
- {"type":"deploy_aws_ec2","repo":"owner/repo","port":"","ref":"main"}
- {"type":"deploy_render","repo":"owner/repo","ref":"main"}
- {"type":"prepare_deployment","repo":"owner/repo","platform":"Azure|Google Cloud|Render","branch":"main"}
- {"type":"aws_ec2_check_space"}
- {"type":"aws_ec2_free_space"}
- {"type":"aws_ec2_resize_volume","add_gb":10,"target_gb":0}
- {"type":"aws_ec2_run_command","command":"...","reason":"why this command is needed"}

For a project upload request use:
{"type":"push_project","repo_name":"","private":false,"commit_message":"..."}

When the user asks you to build, create, or generate a brand-new project from
scratch (they are not uploading an existing project), for example "streamlit
project banavo" or "create a streamlit app that does X", use:
{"type":"generate_project","framework":"streamlit","description":"a detailed spec of exactly what the app should do, based on the whole conversation","repo_name":"optional-repo-name-if-the-user-gave-one"}
The app itself writes the code, creates the files, pushes them to GitHub, and
prepares a deployment link, the same way you would write Streamlit code
yourself. Do not write the project code inside "reply"; only describe the
requested app in "description" and let the app generate and handle the files.

When the user asks to open, show, or go to a repository's settings (in
English, Hindi or Hinglish, e.g. "settings pr jao", "repo settings dikhao"),
use repo_settings with the repository the user means, falling back to the
most recently opened repository if none is named. Only use
update_repo_settings when the user asks to actually change something
(private/public, description, issues/wiki/projects on or off, default
branch, delete-branch-on-merge, topics); only include the fields the user
actually asked to change inside "changes".
Never claim that a GitHub action completed unless the app performs it. The app
always asks for a repository name before project pushing, so do not invent or
assume a repository name.
When the user says open, show, or display a repository, use open_repo instead
of read_file. If the user gives only a short name such as "cancer", pass that
short name; the app resolves it for the connected GitHub account.
For write or destructive actions, return the action but do not claim it is
complete. The app will show a preview and ask the user to confirm.
For deployment requests, use the project analysis to recommend one of these
platforms: Streamlit Cloud, AWS EC2, Azure, Google Cloud, or Render. When the
user says AWS, EC2, Amazon server, or deploy on the EC2 instance, use
`deploy_aws_ec2`. AWS EC2 deployment is a direct Agent-controlled deployment through AWS Systems Manager
(SSM) to the configured EC2 instance. The Agent clones/pulls the GitHub repository,
creates a Streamlit Dockerfile when needed, builds the Docker image on EC2, chooses
an available host port, starts/replaces the container, opens that port in the
instance security group when permitted, and reads the real public IP/hostname and
port from AWS before returning the live URL. Never claim the URL before the AWS
command has completed and the URL has been verified. The Agent must ask for explicit
user approval before deployment. If the user has not clearly chosen a platform, ask
them to choose from the five options. For Streamlit Cloud, prepare the setup link;
do not claim deployment is complete. For Render, if a Render API key is configured
use `deploy_render`: this redeploys the already-linked Render service through the
Render API and returns the real live `serviceDetails.url` once the deploy status is
`live` — never invent or guess the URL. If no Render API key is configured, or no
Render service is linked to the repo yet, fall back to `prepare_deployment` for the
one-click setup link instead. For Azure and Google Cloud, prepare a platform-specific
plan unless a matching direct workflow is available.
Never ask the user to paste an AWS Access Key, AWS Secret Key, AWS session
token, GitHub token/secret, or Render API key directly into the chat message.
AWS credentials are never accepted by this app. If the user asks to "connect
AWS", guide them to the passwordless AWS OIDC setup in the sidebar and ask
only for the non-secret IAM Role ARN returned by their CloudFormation stack.
Likewise for "connect Render" / "Render setup" with no Render API key configured
yet, tell them to use the sidebar's Render guided wizard.

AWS EC2 self-service and diagnostics:
Once AWS OIDC is configured, the Agent controls the EC2 instance
directly through AWS Systems Manager (SSM) — it never needs the user to SSH
in by hand and must never say things like "I can't log into your EC2
directly" or ask the user to run `df -h` themselves. Whenever the user asks
about disk space, "no space left", server health, running containers, or
"is my EC2 ok", use `aws_ec2_check_space` (read-only: disk usage, Docker
image/container disk usage, memory, uptime — runs immediately, no approval
needed). If space is low and the user wants it cleared ("space badhao",
"clean kar do", "free up space"), use `aws_ec2_free_space` (removes unused
Docker images/containers/build cache on the instance to reclaim space — ask
for approval since it changes the instance). If the user wants the disk made
bigger permanently (e.g. "size increase kr do", "disk badha do", "volume
bada karo"), use `aws_ec2_resize_volume` — this actually grows the EC2 root
EBS volume by calling AWS and then extends the filesystem on the instance
(growpart + resize2fs/xfs_growfs), so more space is really available
afterwards; it is not a simulation and not just a suggestion to use the AWS
console. Default to adding 10 GiB (`add_gb: 10`) unless the user names a
specific amount or target size. This changes billed storage, so always ask
for approval first, and never say the Agent "can't resize the volume" or
tell the user to do it themselves in the AWS console/CLI — it can, through
this action. For any other one-off diagnostic or fix command on the EC2
instance that doesn't match a more specific action, use `aws_ec2_run_command` with the exact shell command and
a short "reason"; the app will run it through SSM and ask for approval first
unless the command is clearly read-only (like `df -h`, `docker ps`,
`docker images`, `free -h`, `uptime`, `journalctl`). The deploy action
(`deploy_aws_ec2`) already frees disk space automatically if the instance is
low before it builds, so do not tell the user to manually clear space before
a deployment — just deploy and let the Agent handle it.

If an AWS action fails because the configured AWS keys are missing a
specific IAM permission, the app will detect this itself and ask the user a
plain yes/no question about adding that one permission — do not tell the
user to go create IAM roles/policies by hand; just let that flow happen and,
in your next reply, keep it short (e.g. confirm once they answer yes/no).
"""



def emit_action_event(event: str, status: str, title: str, message: str,
                      action: Optional[Dict[str, Any]] = None,
                      step: int = 1, total_steps: int = 1,
                      data: Optional[Dict[str, Any]] = None) -> None:
    """Append a typed live event. Secrets are never placed in event payloads."""
    action = action or {}
    payload = {
        "request_id": st.session_state.get("active_request_id") or uuid.uuid4().hex,
        "action_id": st.session_state.get("active_action_id") or uuid.uuid4().hex,
        "event": event,
        "status": status,
        "step": step,
        "total_steps": total_steps,
        "title": title,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }
    st.session_state.action_events.append(payload)
    st.session_state.action_events = st.session_state.action_events[-100:]

def set_live_view(kind: str, data: Dict[str, Any]) -> None:
    """Update the right-side 'Live GitHub View' panel snapshot.

    This never stores secrets - only display data (names, paths, states,
    urls) that mirrors what a human would see on github.com while doing the
    same action by hand.
    """
    st.session_state.live_view = {
        "kind": kind,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def action_permission(action: Dict[str, Any]) -> Dict[str, str]:
    t = str(action.get("type", ""))
    read = {
        "list_repos": ("Metadata", "Read", "low"),
        "open_repo": ("Metadata", "Read", "low"),
        "read_file": ("Contents", "Read", "low"),
        "list_issues": ("Issues", "Read", "low"),
        "list_releases": ("Metadata", "Read", "low"),
        "list_workflows": ("Actions", "Read", "low"),
        "analyze_project": ("Contents", "Read", "low"),
        "repo_settings": ("Administration", "Read", "low"),
    }
    if t in read:
        scope, access, risk = read[t]
    elif t == "update_repo_settings":
        scope, access, risk = ("Administration", "Write", "high")
    elif t in {"run_workflow", "deploy_aws"}:
        scope, access, risk = ("Actions", "Write", "medium")
    elif t == "deploy_aws_ec2":
        scope, access, risk = ("AWS EC2 + SSM", "Deploy", "high")
    elif t == "aws_ec2_check_space":
        scope, access, risk = ("AWS EC2 + SSM", "Read", "low")
    elif t == "aws_ec2_free_space":
        scope, access, risk = ("AWS EC2 + SSM", "Write", "medium")
    elif t == "aws_ec2_resize_volume":
        scope, access, risk = ("AWS EC2 (EBS)", "Write", "high")
    elif t == "aws_ec2_run_command":
        scope, access, risk = ("AWS EC2 + SSM", "Write", "medium")
    elif t == "aws_grant_permission":
        scope, access, risk = ("AWS IAM", "Write", "high")
    elif t in {"create_issue", "update_issue", "close_issue"}:
        scope, access, risk = ("Issues", "Write", "medium")
    elif t in {"create_pull_request", "close_pull_request"}:
        scope, access, risk = ("Pull requests", "Write", "medium")
    elif t == "merge_pull_request":
        scope, access, risk = ("Pull requests", "Write", "high")
    elif t in {"update_file", "delete_file", "push_project"}:
        scope, access, risk = ("Contents", "Write", "high" if t == "delete_file" else "medium")
    elif t == "create_branch":
        scope, access, risk = ("Contents", "Write", "medium")
    elif t in {"create_repo", "rename_repo", "delete_repo"}:
        scope, access, risk = ("Administration", "Write", "high")
    elif t in {"create_release", "delete_release"}:
        scope, access, risk = ("Contents", "Write", "high" if t == "delete_release" else "medium")
    elif t == "prepare_deployment":
        scope, access, risk = ("Actions", "Write", "medium")
    elif t == "deploy_render":
        scope, access, risk = ("Render API", "Deploy", "medium")
    else:
        scope, access, risk = ("GitHub API", "Write", "high")
    return {"scope": scope, "access": access, "risk": risk}

def approval_description(action: Dict[str, Any]) -> str:
    p = action_permission(action)
    repo = action.get("repo") or action.get("repo_name") or "—"
    affected = action.get("path") or action.get("workflow") or action.get("number") or "repository resource"
    return (
        f"**Action:** `{action.get('type','unknown')}`  \n"
        f"**Repository:** `{repo}`  \n"
        f"**Affected:** `{affected}`  \n"
        f"**Required permission:** **{p['scope']}: {p['access']}**  \n"
        f"**Risk:** **{p['risk'].upper()}**  \n"
        f"**Reason:** GitHub must authorize this operation; the Agent will not bypass token permissions."
    )

def init_state() -> None:
    defaults = {
        "active_request_id": None,
        "active_action_id": None,
        "gh_client": None,
        "gh_user": None,
        "github_access_token": "",
        "project_files": [],
        "project_zip_name": None,
        "uploaded_signature": None,
        "project_analysis": None,
        "last_push_report": None,
        "pending_push": False,
        "pending_file_push_files": None,
        "pending_confirmation": None,
        "last_pushed_repo": None,
        "auto_deploy_after_push": False,
        "deployment_platform": ASK_DEPLOYMENT_PLATFORM,
        "ec2_runner_label": os.getenv("EC2_RUNNER_LABEL", "ec2-deployer"),
        "ec2_default_port": "",
        "aws_region": os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")),
        # Deliberately NOT defaulted from EC2_INSTANCE_ID / EC2_SECURITY_GROUP_ID /
        # AWS_OIDC_ROLE_ARN env vars, even if the developer has set them for their
        # own personal/testing use. Those would be the *developer's own* target
        # instance and role. Every visitor session must start fully disconnected
        # and only ever use the Role ARN *that visitor* pastes after running the
        # CloudFormation stack in *their own* AWS account — otherwise a fresh
        # visitor could silently show as "AWS connected" and have the Agent write
        # the developer's own role ARN into that visitor's GitHub Actions workflow
        # without them ever clicking "Connect my AWS account".
        "aws_ec2_instance_id": "",
        "aws_security_group_id": "",
        "aws_oidc_role_arn": "",
        "aws_account_id_input": "",
        "aws_already_has_oidc_provider": False,
        "aws_already_has_ec2": False,
        "aws_oidc_connected": False,
        "aws_chat_wizard_active": False,
        "aws_setup_token": "",
        "aws_setup_pending": False,
        "render_api_key": "",
        "render_verified_once": False,
        "render_chat_wizard_active": False,
        "render_wizard_step": 0,
        "pending_deployment": None,
        "provider": "OpenAI",
        "api_key": "",
        "voice_provider": "",
        "voice_api_key": "",
        "model": PROVIDERS["OpenAI"]["model"],
        "custom_endpoint": "",
        "private_repo": True,
        "commit_message": "Initial commit via GitHub Agent",
        "action_events": [],
        "action_history": [],
        "live_view": None,
        "current_repo": None,
        "github_oauth_client_id": _ENV_GITHUB_CLIENT_ID,
        "github_oauth_client_secret": _ENV_GITHUB_CLIENT_SECRET,
        "github_oauth_redirect_uri": _ENV_PUBLIC_APP_URL or "http://localhost:8501",
        "github_oauth_state": uuid.uuid4().hex,
        "github_oauth_error": None,
        "auto_speak": True,
        "messages": [
            {
                "role": "assistant",
                "content": (
                    "Hello! I'm your GitHub Agent.\n\n"
                    "You can talk to me in English, Hindi, Hinglish, or "
                    "any language you like. Connect your GitHub account once, "
                    "then upload your project if needed and tell me what to do. "
                    "For AWS, connect your own AWS account once using secure "
                    "GitHub OIDC — no AWS access keys are required.\n\n"
                    "In the **Live GitHub View** tab on the right, you'll "
                    "see what I'm doing on GitHub — repo, settings, "
                    "issues, releases, all in real-time."
                ),
            }
        ],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def sync_provider_model() -> None:
    st.session_state.model = PROVIDERS[st.session_state.provider]["model"]


def selected_model() -> str:
    provider = PROVIDERS[st.session_state.provider]
    model = str(st.session_state.model).strip()
    # A common setup mistake is pasting an environment-variable name or API
    # key into the model field. Recover with the provider's known-good default.
    if not model or "API_KEY" in model.upper() or model.startswith(("sk-", "key-")):
        model = provider["model"]
        st.session_state.model = model
    return model


def safe_zip_path(name: str) -> Optional[str]:
    normalized = name.replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 50 * 1024 * 1024


def collect_zip_files(
    raw_zip: bytes,
    files: List[Dict[str, Any]],
    total_bytes: int,
    prefix: str = "",
    depth: int = 0,
) -> int:
    """Extract a ZIP into memory, including nested ZIPs up to two levels."""
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = safe_zip_path(info.filename)
            if not path:
                continue
            content = archive.read(info)
            if (
                len(content) > MAX_UPLOAD_FILE_BYTES
                and not path.lower().endswith(".zip")
            ):
                raise ValueError(f"`{path}` is bigger than 10 MB.")
            full_path = f"{prefix}/{path}".strip("/")
            if path.lower().endswith(".zip") and depth < 2:
                try:
                    total_bytes = collect_zip_files(
                        content,
                        files,
                        total_bytes,
                        prefix=full_path[:-4].rstrip("/"),
                        depth=depth + 1,
                    )
                    continue
                except zipfile.BadZipFile:
                    # A non-archive .zip is still a valid uploaded file.
                    pass
            total_bytes += len(content)
            if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
                raise ValueError("Uploaded files' total size is more than 50 MB.")
            files.append({"path": full_path, "content": content})
    return total_bytes


def load_uploaded_files(uploaded_files: List[Any]) -> None:
    try:
        files: List[Dict[str, Any]] = []
        total_bytes = 0
        upload_names: List[str] = []
        for uploaded_file in uploaded_files:
            raw = uploaded_file.getvalue()
            upload_names.append(uploaded_file.name)
            safe_name = safe_zip_path(uploaded_file.name)
            if not safe_name:
                continue
            is_archive = safe_name.lower().endswith(".zip")
            if len(raw) > MAX_UPLOAD_TOTAL_BYTES:
                raise ValueError(f"`{uploaded_file.name}` is bigger than 50 MB.")
            if len(raw) > MAX_UPLOAD_FILE_BYTES and not is_archive:
                raise ValueError(f"`{uploaded_file.name}` is bigger than 10 MB.")
            if is_archive:
                try:
                    total_bytes = collect_zip_files(raw, files, total_bytes)
                    continue
                except zipfile.BadZipFile:
                    pass
            total_bytes += len(raw)
            if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
                raise ValueError("Uploaded files' total size is more than 50 MB.")
            files.append({"path": safe_name, "content": raw})

        if not files:
            raise ValueError("No readable files found in the upload.")
        st.session_state.project_files = files
        st.session_state.project_zip_name = ", ".join(upload_names)
        st.session_state.uploaded_signature = hashlib.sha256(
            b"".join(
                uploaded_file.getvalue()
                for uploaded_file in uploaded_files
            )
        ).hexdigest()
        st.session_state.project_analysis = analyze_project_files(files)
        st.session_state.last_push_report = None
        st.session_state.pending_push = False
        st.success(
            f"{len(files)} files loaded ({len(upload_names)} upload"
            f"{'s' if len(upload_names) != 1 else ''})"
        )
    except (zipfile.BadZipFile, ValueError) as exc:
        st.error(f"Failed to load upload: {exc}")
    except Exception as exc:
        st.error(f"Error reading files: {exc}")


def analyze_project_files(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Inspect project metadata without executing uploaded code."""
    names = [str(item["path"]) for item in files]
    lowered_names = [name.lower() for name in names]
    text_parts: List[str] = []
    for item in files:
        path = str(item["path"]).lower()
        if path.endswith((".py", ".txt", ".toml", ".yaml", ".yml", ".md")):
            text_parts.append(bytes(item["content"])[:200_000].decode("utf-8", errors="ignore").lower())
    combined = "\n".join(text_parts)

    framework = "Unknown"
    if "streamlit" in combined:
        framework = "Streamlit"
    elif "fastapi" in combined:
        framework = "FastAPI"
    elif "flask" in combined:
        framework = "Flask"
    elif "django" in combined or "manage.py" in lowered_names:
        framework = "Django"
    elif "package.json" in lowered_names or "express" in combined:
        framework = "Node.js"

    preferred = {
        "Streamlit": ("app.py", "streamlit_app.py", "main.py"),
        "FastAPI": ("main.py", "app.py", "server.py"),
        "Flask": ("app.py", "main.py", "server.py"),
        "Django": ("manage.py", "wsgi.py", "asgi.py"),
        "Node.js": ("server.js", "index.js", "main.js", "app.js"),
    }
    python_files = [
        item["path"] for item in files if str(item["path"]).lower().endswith(".py")
    ]
    javascript_files = [
        item["path"]
        for item in files
        if str(item["path"]).lower().endswith((".js", ".mjs", ".cjs", ".ts"))
    ]
    entrypoint_files = python_files + javascript_files
    entrypoint = next(
        (
            path
            for candidate in preferred.get(framework, ())
            for path in entrypoint_files
            if path.rsplit("/", 1)[-1].lower() == candidate
        ),
        entrypoint_files[0] if entrypoint_files else None,
    )
    dependency_file = next(
        (
            path
            for path in lowered_names
            if path.rsplit("/", 1)[-1] in {
                "requirements.txt",
                "pyproject.toml",
                "poetry.lock",
                "package.json",
                "pnpm-lock.yaml",
                "yarn.lock",
            }
        ),
        None,
    )
    dockerfile = next(
        (path for path in names if path.rsplit("/", 1)[-1].lower() == "dockerfile"),
        None,
    )
    procfile = next(
        (path for path in names if path.rsplit("/", 1)[-1].lower() == "procfile"),
        None,
    )
    workflow_files = [
        path for path in names if path.lower().startswith(".github/workflows/")
    ]
    has_frontend = any(
        marker in combined
        for marker in ("react", "next", "vite", "vue", "angular")
    ) or "package.json" in lowered_names
    if framework == "Unknown" and has_frontend:
        framework = "Node.js / frontend"

    analysis = {
        "framework": framework,
        "entrypoint": entrypoint,
        "dependency_file": dependency_file,
        "file_count": len(files),
        "dockerfile": dockerfile,
        "procfile": procfile,
        "workflow_files": workflow_files,
        "has_frontend": has_frontend,
        "has_python": bool(python_files),
        "has_node": bool(javascript_files) or "package.json" in lowered_names,
    }
    recommendation = recommend_deployment_platform(analysis)
    analysis["recommended_platform"] = recommendation["platform"]
    analysis["recommendation_reason"] = recommendation["reason"]
    return analysis


def recommend_deployment_platform(analysis: Dict[str, Any]) -> Dict[str, str]:
    """Recommend a deployment target from detected project characteristics."""
    framework = str(analysis.get("framework") or "Unknown").lower()
    has_docker = bool(analysis.get("dockerfile"))
    has_workflows = bool(analysis.get("workflow_files"))

    if "streamlit" in framework:
        return {
            "platform": "Streamlit Cloud",
            "reason": "Streamlit app detected; it is the simplest fit and needs minimal deployment setup.",
        }
    if has_docker:
        return {
            "platform": "Render",
            "reason": "A Dockerfile is present, so Render can run the project with a straightforward web service setup.",
        }
    if any(name in framework for name in ("fastapi", "flask", "django", "node")):
        return {
            "platform": "Render",
            "reason": f"{analysis.get('framework')} web project detected; Render is the quickest low-configuration option.",
        }
    if has_workflows:
        return {
            "platform": "AWS",
            "reason": "Existing GitHub Actions workflows suggest a CI/CD-oriented deployment; AWS is a strong production choice.",
        }
    if analysis.get("has_frontend"):
        return {
            "platform": "Render",
            "reason": "A frontend/Node project was detected; Render provides a simple build-and-deploy path.",
        }
    return {
        "platform": "Render",
        "reason": "The project type is not fully identifiable yet; Render is the safest general-purpose starting point.",
    }


def deployment_platform_prompt(analysis: Optional[Dict[str, Any]]) -> str:
    if not analysis:
        return (
            "Upload the project ZIP before deployment so I can scan the files and "
            "suggest the best platform."
        )
    recommended = analysis.get("recommended_platform", "Render")
    reason = analysis.get(
        "recommendation_reason",
        "General-purpose deployment option for the project.",
    )
    options = "\n".join(
        f"{index}. **{platform}**"
        for index, platform in enumerate(DEPLOYMENT_PLATFORM_OPTIONS, start=1)
    )
    return (
        f"After analyzing the project files, my recommendation is: **{recommended}** — {reason}\n\n"
        "Which deployment platform would you like to use? Reply with a number or name:\n"
        f"{options}\n\n"
        "For AWS EC2, reply **AWS** or **2**. After that the Agent will ask for deployment permission."
    )


def gh_analyze_repo(repo_name: str) -> Dict[str, Any]:
    """Read repository metadata for deployment advice without executing code."""
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    files: List[Dict[str, Any]] = []
    relevant_suffixes = (
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".txt",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".md",
    )

    def walk(path: str = "", depth: int = 0) -> None:
        if depth > 3 or len(files) >= 150:
            return
        entries = repo.get_contents(path, ref=repo.default_branch)
        if not isinstance(entries, list):
            entries = [entries]
        for entry in entries:
            if len(files) >= 150:
                return
            if entry.type == "dir" and (
                not entry.name.startswith(".") or entry.name == ".github"
            ):
                walk(entry.path, depth + 1)
            elif entry.type == "file":
                content = b""
                if entry.path.lower().endswith(relevant_suffixes) and entry.size <= 250_000:
                    content = entry.decoded_content
                files.append({"path": entry.path, "content": content})

    walk()
    if not files:
        return {
            "framework": "Unknown",
            "entrypoint": None,
            "dependency_file": None,
            "file_count": 0,
        }
    return analyze_project_files(files)


def project_analysis_text(analysis: Optional[Dict[str, Any]]) -> str:
    if not analysis:
        return "Upload the project ZIP first."
    return (
        "**Project analysis complete:**\n"
        f"- Framework: `{analysis.get('framework', 'Unknown')}`\n"
        f"- Entrypoint: `{analysis.get('entrypoint') or 'Not detected'}`\n"
        f"- Dependency file: `{analysis.get('dependency_file') or 'Not detected'}`\n"
        f"- Files scanned: `{analysis.get('file_count', 0)}`\n"
        f"- Dockerfile: `{'Detected' if analysis.get('dockerfile') else 'Not detected'}`\n"
        f"- Recommended deployment: **{analysis.get('recommended_platform', 'Render')}**\n"
        f"- Why: {analysis.get('recommendation_reason', 'General-purpose option for the project.')}"
    )


def github_error(exc: Exception) -> str:
    if isinstance(exc, GithubException):
        data = exc.data if isinstance(exc.data, dict) else {}
        message = str(data.get("message", str(exc)))
        if exc.status in (401, 403) and (
            "resource not accessible" in message.lower()
            or "bad credentials" in message.lower()
            or "permission" in message.lower()
        ):
            return (
                f"{message} (HTTP {exc.status}). Your GitHub token may be valid, "
                "but this action's permission is missing. To create a personal repo, "
                "use the `repo` scope in a classic PAT; deletion also needs "
                "`delete_repo`. For a fine-grained PAT, select the correct resource "
                "owner and grant Administration/Contents/Issues/Pull requests "
                "permissions, then reconnect with the new token. Organization "
                "policy may also block repo creation/deletion."
            )
        return f"{message} (HTTP {exc.status})" if exc.status else message
    return str(exc)


def gh_push_project(repo_name: str, private: bool, commit_message: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", repo_name):
        return "Invalid repo name. Only use letters, numbers, `.`, `_` and `-`."
    if not st.session_state.project_files:
        return "Upload your project files or ZIP in the sidebar first."
    if st.session_state.gh_client is None or st.session_state.gh_user is None:
        return "Connect GitHub from the sidebar first."

    client = st.session_state.gh_client
    user = st.session_state.gh_user
    full_name = f"{user.login}/{repo_name}"

    try:
        repo = client.get_repo(full_name)
        created = False
    except GithubException as exc:
        # Only a real 404 means the repository is absent. A 401/403 must not
        # fall through to create_repo because that hides the actual permission
        # problem and produces a misleading "create repository" error.
        if exc.status != 404:
            return f"Failed to check existing repository: {github_error(exc)}"
        try:
            repo = user.create_repo(repo_name, private=private)
            created = True
        except Exception as create_exc:
            return f"Could not create repository: {github_error(create_exc)}"

    created_count = 0
    updated_count = 0
    created_paths: List[str] = []
    updated_paths: List[str] = []
    failures: List[str] = []
    for project_file in st.session_state.project_files:
        path = project_file["path"]
        content = project_file["content"]
        try:
            try:
                existing = repo.get_contents(path)
                if isinstance(existing, list):
                    raise GithubException(400, {"message": "Path is a directory"})
                repo.update_file(path, commit_message, content, existing.sha)
                updated_count += 1
                updated_paths.append(path)
            except GithubException as exc:
                # Only a 404 means the file is new. Do not turn 401/403,
                # rate-limit, or conflict errors into a misleading create call.
                if exc.status != 404:
                    raise
                repo.create_file(path, commit_message, content)
                created_count += 1
                created_paths.append(path)
        except Exception as exc:
            failures.append(f"{path}: {github_error(exc)}")

    result = (
        f"{'Created a new repo and ' if created else 'In the existing repo, '}"
        f"pushed the project.\n\n"
        f"**Repository:** [{repo.full_name}]({repo.html_url})\n"
        f"- New files: {created_count}\n"
        f"- Updated files: {updated_count}"
    )
    st.session_state.last_pushed_repo = repo.full_name
    st.session_state.last_push_report = {
        "repo": repo.full_name,
        "created": created_paths,
        "updated": updated_paths,
        "failed": failures,
    }
    if created_paths:
        result += "\n\n**Created files:**\n" + "\n".join(
            f"- ✅ `{path}`" for path in created_paths
        )
    if updated_paths:
        result += "\n\n**Updated files:**\n" + "\n".join(
            f"- 🔄 `{path}`" for path in updated_paths
        )
    if failures:
        result += "\n\n**Failed files:**\n" + "\n".join(
            f"- ❌ `{failure}`" for failure in failures
        )
    return result


def gh_list_repos() -> str:
    user = st.session_state.gh_user
    repos = list(user.get_repos())[:30]
    if not repos:
        set_live_view("repos_list", {"owner": user.login, "repos": []})
        return "No repositories found in your account."
    set_live_view("repos_list", {
        "owner": user.login,
        "repos": [
            {
                "full_name": repo.full_name,
                "private": repo.private,
                "description": repo.description or "",
                "stars": repo.stargazers_count,
                "html_url": repo.html_url,
            }
            for repo in repos
        ],
    })
    # Keep the chat response short and conversational. The complete list is
    # already visible in the Live GitHub View, so do not make the voice agent
    # read every repository name aloud.
    private_count = sum(1 for repo in repos if repo.private)
    visibility = f"{private_count} private" if private_count else "all public"
    return (
        f"Shubham, your GitHub repositories list is open — {len(repos)} repos in total ({visibility}). "
        "Take a look at the list and tell me which project to deploy."
    )


def resolve_repo_name(repo_name: str) -> str:
    cleaned = repo_name.strip().strip("`'\"")
    if "/" not in cleaned and st.session_state.gh_user is not None:
        return f"{st.session_state.gh_user.login}/{cleaned}"
    return cleaned


def gh_open_repo(repo_name: str) -> str:
    full_name = resolve_repo_name(repo_name)
    repo = st.session_state.gh_client.get_repo(full_name)
    contents = repo.get_contents("")
    entries: List[Dict[str, Any]] = []
    if isinstance(contents, list):
        for item in sorted(contents, key=lambda entry: (entry.type != "dir", entry.name.lower()))[:40]:
            entries.append({
                "name": item.name,
                "type": item.type,
                "path": item.path,
                "url": item.html_url,
            })
    else:
        entries.append({
            "name": contents.name,
            "type": contents.type,
            "path": contents.path,
            "url": contents.html_url,
        })

    readme_text = ""
    try:
        readme = repo.get_readme()
        readme_text = readme.decoded_content.decode("utf-8", errors="replace")[:3000]
    except GithubException:
        readme_text = ""

    st.session_state.current_repo = repo.full_name
    set_live_view("repo", {
        "full_name": repo.full_name,
        "description": repo.description or "",
        "default_branch": repo.default_branch,
        "private": repo.private,
        "stars": repo.stargazers_count,
        "forks": repo.forks_count,
        "open_issues": repo.open_issues_count,
        "html_url": repo.html_url,
        "entries": entries,
        "readme": readme_text,
    })

    items = []
    for entry in entries:
        icon = "📁" if entry["type"] == "dir" else "📄"
        items.append(f"- {icon} [{entry['name']}]({entry['url']})")
    listing = "\n".join(items) or "- Repository is empty."
    return (
        f"**Repository:** [{repo.full_name}]({repo.html_url})\n\n"
        f"**Description:** {repo.description or 'No description'}\n\n"
        f"**Default branch:** `{repo.default_branch}`\n\n"
        f"**Top-level files/folders:**\n{listing}\n\n"
        f"👉 The **Live GitHub View** panel on the right also shows this repo screen live."
    )


def gh_create_repo(
    name: str,
    private: bool = False,
    description: str = "",
    owner: str = "",
) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        return "Invalid repo name."
    target_owner = owner.strip().strip("@")
    if target_owner and target_owner.lower() != st.session_state.gh_user.login.lower():
        organization = st.session_state.gh_client.get_organization(target_owner)
        repo = organization.create_repo(
            name, private=private, description=description or ""
        )
    else:
        repo = st.session_state.gh_user.create_repo(
            name, private=private, description=description or ""
        )
    return f"Repository created: [{repo.full_name}]({repo.html_url})"


def normalize_repo_name(name: str) -> str:
    """Convert a natural-language repo name into a valid GitHub repo name."""
    cleaned = name.strip().strip("`'\".,:;")
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned


def gh_rename_repo(repo_name: str, new_name: str) -> str:
    normalized_name = normalize_repo_name(new_name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", normalized_name):
        return (
            "Invalid new repo name. Use only letters, numbers, `.`, `_` and `-`."
        )

    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    old_full_name = repo.full_name
    repo.edit(name=normalized_name)
    return (
        f"Repository renamed: [{repo.full_name}]({repo.html_url})\n\n"
        f"Old name: `{old_full_name}`\n"
        f"New name: `{repo.full_name}`"
    )


def gh_list_issues(repo_name: str) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    issues = list(repo.get_issues(state="open"))[:20]
    set_live_view("issues", {
        "full_name": repo.full_name,
        "issues": [
            {
                "number": issue.number,
                "title": issue.title,
                "state": issue.state,
                "html_url": issue.html_url,
                "is_pull_request": issue.pull_request is not None,
            }
            for issue in issues
        ],
    })
    if not issues:
        return f"{repo_name} has no open issues."
    return "\n".join(f"- #{issue.number} {issue.title}" for issue in issues)


def gh_read_file(repo_name: str, path: str) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    content = repo.get_contents(path)
    if isinstance(content, list):
        return "This path is a folder, not a file."
    text = content.decoded_content.decode("utf-8", errors="replace")
    set_live_view("file", {
        "full_name": repo.full_name,
        "path": content.path,
        "html_url": content.html_url,
        "content": text[:5000],
        "truncated": len(text) > 5000,
    })
    if len(text) > 5000:
        text = text[:5000] + "\n...(truncated)"
    return f"```text\n{text}\n```"


def gh_create_issue(repo_name: str, title: str, body: str = "") -> str:
    issue = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name)).create_issue(
        title=title,
        body=body or "",
    )
    return f"Issue created: [#{issue.number}]({issue.html_url})"


def gh_update_issue(
    repo_name: str,
    number: int,
    title: str = "",
    body: str = "",
    state: str = "",
) -> str:
    issue = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name)).get_issue(number)
    changes: Dict[str, str] = {}
    if title.strip():
        changes["title"] = title.strip()
    if body.strip():
        changes["body"] = body
    if state.strip().lower() in {"open", "closed"}:
        changes["state"] = state.strip().lower()
    if not changes:
        return "Provide a title, body, or state to update the issue."
    issue.edit(**changes)
    return f"Issue #{number} updated."


def gh_close_pull_request(repo_name: str, number: int) -> str:
    pull_request = st.session_state.gh_client.get_repo(
        resolve_repo_name(repo_name)
    ).get_pull(number)
    pull_request.edit(state="closed")
    return f"Pull request #{number} closed."


def gh_update_file(repo_name: str, path: str, content: str, commit_message: str) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    existing = repo.get_contents(path)
    if isinstance(existing, list):
        return "This path is a folder, not a file."
    repo.update_file(path, commit_message, content, existing.sha)
    return f"File updated: [{path}]({existing.html_url})"


def gh_update_files_subset(
    repo_name: str, files: List[Dict[str, Any]], commit_message: str
) -> str:
    """Commit only the given files to an existing repo — nothing else in the
    repo is touched, unlike gh_push_project which pushes the whole
    st.session_state.project_files list. Used for files attached directly
    in the chat box when the user only wants one (or a few) file changed."""
    if not files:
        return "No file was attached."
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))

    created_paths: List[str] = []
    updated_paths: List[str] = []
    failures: List[str] = []
    for project_file in files:
        path = project_file["path"]
        content = project_file["content"]
        try:
            try:
                existing = repo.get_contents(path)
                if isinstance(existing, list):
                    raise GithubException(400, {"message": "Path is a directory"})
                repo.update_file(path, commit_message, content, existing.sha)
                updated_paths.append(path)
            except GithubException as exc:
                if exc.status != 404:
                    raise
                repo.create_file(path, commit_message, content)
                created_paths.append(path)
        except Exception as exc:
            failures.append(f"{path}: {github_error(exc)}")

    result = (
        f"Updated {len(files)} attached "
        f"file{'s' if len(files) != 1 else ''} in repository `{repo.full_name}` — the rest of the repo is untouched.\n\n"
        f"**Repository:** [{repo.full_name}]({repo.html_url})"
    )
    if created_paths:
        result += "\n\n**Created files:**\n" + "\n".join(
            f"- ✅ `{path}`" for path in created_paths
        )
    if updated_paths:
        result += "\n\n**Updated files:**\n" + "\n".join(
            f"- 🔄 `{path}`" for path in updated_paths
        )
    if failures:
        result += "\n\n**Failed files:**\n" + "\n".join(
            f"- ❌ `{failure}`" for failure in failures
        )
    st.session_state.last_pushed_repo = repo.full_name
    return result


def gh_delete_file(repo_name: str, path: str, commit_message: str) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    existing = repo.get_contents(path)
    if isinstance(existing, list):
        return "This path is a folder, not a file."
    repo.delete_file(path, commit_message, existing.sha)
    return f"File deleted: `{path}`"


def gh_create_branch(repo_name: str, branch: str, from_branch: str = "") -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    source = from_branch or repo.default_branch
    sha = repo.get_branch(source).commit.sha
    repo.create_git_ref(ref=f"refs/heads/{branch}", sha=sha)
    return f"Branch `{branch}` created, from source `{source}`."


def gh_create_pull_request(
    repo_name: str,
    title: str,
    head: str,
    base: str = "",
    body: str = "",
) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    pull_request = repo.create_pull(
        title=title,
        body=body or "",
        head=head,
        base=base or repo.default_branch,
    )
    return f"Pull request created: [#{pull_request.number}]({pull_request.html_url})"


def gh_merge_pull_request(repo_name: str, number: int, commit_message: str = "") -> str:
    pull_request = st.session_state.gh_client.get_repo(
        resolve_repo_name(repo_name)
    ).get_pull(number)
    result = pull_request.merge(commit_message=commit_message or None)
    if not result.merged:
        return f"Pull request could not be merged: {result.message}"
    return f"Pull request #{number} merged."


def gh_close_issue(repo_name: str, number: int) -> str:
    issue = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name)).get_issue(number)
    issue.edit(state="closed")
    return f"Issue #{number} closed."


def gh_delete_repo(repo_name: str) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    full_name = repo.full_name
    repo.delete()
    return f"Repository `{full_name}` deleted."


def gh_list_releases(repo_name: str) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    releases = list(repo.get_releases())[:20]
    set_live_view("releases", {
        "full_name": repo.full_name,
        "releases": [
            {
                "tag_name": release.tag_name,
                "title": release.title or release.name or "",
                "html_url": release.html_url,
                "draft": release.draft,
                "prerelease": release.prerelease,
            }
            for release in releases
        ],
    })
    if not releases:
        return f"{repo_name} has no releases."
    return "\n".join(
        f"- `{release.tag_name}` {release.title or release.name or ''} "
        f"({release.html_url})"
        for release in releases
    )


def gh_create_release(
    repo_name: str,
    tag: str,
    name: str = "",
    body: str = "",
    draft: bool = False,
    prerelease: bool = False,
) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", tag.strip()):
        return "Invalid release tag."
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    release = repo.create_git_release(
        tag=tag.strip(),
        name=name.strip() or tag.strip(),
        message=body or "",
        draft=draft,
        prerelease=prerelease,
    )
    return f"Release created: [{release.tag_name}]({release.html_url})"


def gh_delete_release(repo_name: str, release_id: int) -> str:
    if release_id <= 0:
        return "A valid release_id is required."
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    release = repo.get_release(release_id)
    tag_name = release.tag_name
    release.delete()
    return f"Release `{tag_name}` deleted."


def gh_list_workflows(repo_name: str) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    workflows = list(repo.get_workflows())[:30]
    set_live_view("workflows", {
        "full_name": repo.full_name,
        "workflows": [
            {"name": workflow.name, "state": workflow.state, "id": workflow.id}
            for workflow in workflows
        ],
    })
    if not workflows:
        return f"{repo_name} has no GitHub Actions workflows."
    return "\n".join(
        f"- `{workflow.name}` — {workflow.state} (id: {workflow.id})"
        for workflow in workflows
    )


def gh_repo_settings(repo_name: str) -> str:
    """Read the repository's real GitHub settings, the same as the Settings tab shows."""
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    st.session_state.current_repo = repo.full_name
    settings_data = {
        "full_name": repo.full_name,
        "description": repo.description or "",
        "default_branch": repo.default_branch,
        "private": repo.private,
        "has_issues": repo.has_issues,
        "has_wiki": repo.has_wiki,
        "has_projects": repo.has_projects,
        "has_discussions": getattr(repo, "has_discussions", False),
        "allow_merge_commit": getattr(repo, "allow_merge_commit", True),
        "allow_squash_merge": getattr(repo, "allow_squash_merge", True),
        "allow_rebase_merge": getattr(repo, "allow_rebase_merge", True),
        "delete_branch_on_merge": getattr(repo, "delete_branch_on_merge", False),
        "topics": repo.get_topics() if hasattr(repo, "get_topics") else [],
        "html_url": repo.html_url,
        "settings_url": f"{repo.html_url}/settings",
    }
    set_live_view("settings", settings_data)
    lines = [
        f"**Repository settings:** [{repo.full_name}]({settings_data['settings_url']})",
        f"- Visibility: **{'Private' if settings_data['private'] else 'Public'}**",
        f"- Default branch: `{settings_data['default_branch']}`",
        f"- Description: {settings_data['description'] or '_(none)_'}",
        f"- Issues: {'Enabled' if settings_data['has_issues'] else 'Disabled'}",
        f"- Wiki: {'Enabled' if settings_data['has_wiki'] else 'Disabled'}",
        f"- Projects: {'Enabled' if settings_data['has_projects'] else 'Disabled'}",
        f"- Delete branch on merge: {'Yes' if settings_data['delete_branch_on_merge'] else 'No'}",
        f"- Topics: {', '.join(settings_data['topics']) if settings_data['topics'] else '_(none)_'}",
        "",
        "👉 The **Live GitHub View** on the right also shows this like a Settings page.",
    ]
    return "\n".join(lines)


def gh_update_repo_settings(repo_name: str, changes: Dict[str, Any]) -> str:
    """Apply requested settings changes exactly the way GitHub's Settings page would."""
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    edit_kwargs: Dict[str, Any] = {}
    applied: List[str] = []

    if "private" in changes:
        edit_kwargs["private"] = bool(changes["private"])
        applied.append(f"Visibility -> {'Private' if changes['private'] else 'Public'}")
    if "description" in changes:
        edit_kwargs["description"] = str(changes["description"])
        applied.append("Description update")
    if "default_branch" in changes:
        edit_kwargs["default_branch"] = str(changes["default_branch"])
        applied.append(f"Default branch -> {changes['default_branch']}")
    if "has_issues" in changes:
        edit_kwargs["has_issues"] = bool(changes["has_issues"])
        applied.append(f"Issues -> {'Enabled' if changes['has_issues'] else 'Disabled'}")
    if "has_wiki" in changes:
        edit_kwargs["has_wiki"] = bool(changes["has_wiki"])
        applied.append(f"Wiki -> {'Enabled' if changes['has_wiki'] else 'Disabled'}")
    if "has_projects" in changes:
        edit_kwargs["has_projects"] = bool(changes["has_projects"])
        applied.append(f"Projects -> {'Enabled' if changes['has_projects'] else 'Disabled'}")
    if "delete_branch_on_merge" in changes:
        edit_kwargs["delete_branch_on_merge"] = bool(changes["delete_branch_on_merge"])
        applied.append(
            f"Delete branch on merge -> {'Yes' if changes['delete_branch_on_merge'] else 'No'}"
        )

    if not edit_kwargs and "topics" not in changes:
        return "Clearly say what to change in Settings (e.g. private/public, description, issues on/off)."

    if edit_kwargs:
        repo.edit(**edit_kwargs)
    if "topics" in changes and isinstance(changes["topics"], list):
        repo.replace_topics([str(topic) for topic in changes["topics"]])
        applied.append("Topics update")

    # Refresh the live view with the settings GitHub now actually has.
    gh_repo_settings(repo.full_name)
    return "Settings updated:\n" + "\n".join(f"- {line}" for line in applied)


def gh_run_workflow(
    repo_name: str,
    workflow: str,
    ref: str = "",
    inputs: Optional[Dict[str, Any]] = None,
) -> str:
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    workflow_name = workflow.strip()
    if not workflow_name:
        return "A workflow file or workflow id is required."
    workflow_obj = repo.get_workflow(workflow_name)
    workflow_obj.create_dispatch(ref=ref.strip() or repo.default_branch, inputs=inputs or {})
    return f"Workflow `{workflow_name}` triggered."


EC2_DEPLOY_WORKFLOW_PATH = ".github/workflows/deploy-aws-ec2.yml"
EC2_DEPLOY_WORKFLOW_MARKER = "Deploy to AWS EC2 (self-hosted)"

def _load_ec2_workflow_template() -> str:
    path = Path(__file__).resolve().parent / EC2_DEPLOY_WORKFLOW_PATH
    return path.read_text(encoding="utf-8")

def _get_github_token_for_api() -> str:
    client = st.session_state.get("gh_client")
    requester = getattr(client, "_Github__requester", None)
    auth = getattr(requester, "_Requester__auth", None) if requester else None
    return str(getattr(auth, "token", "") or "") if auth else ""

def _github_rest(method: str, url: str, **kwargs: Any) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    headers["Accept"] = "application/vnd.github+json"
    token = _get_github_token_for_api()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(method, url, headers=headers, timeout=30, **kwargs)

def gh_ensure_ec2_workflow(repo: Any, branch: str) -> Tuple[bool, str]:
    content = _load_ec2_workflow_template()
    try:
        existing = repo.get_contents(EC2_DEPLOY_WORKFLOW_PATH, ref=branch)
        if isinstance(existing, list):
            return False, "EC2 deployment workflow path is a directory."
        decoded = existing.decoded_content.decode("utf-8", errors="replace")
        if EC2_DEPLOY_WORKFLOW_MARKER in decoded:
            return True, "Managed AWS EC2 deployment workflow already exists."
        return False, f"`{EC2_DEPLOY_WORKFLOW_PATH}` already exists and is not managed by this Agent, so it was not overwritten."
    except GithubException as exc:
        if exc.status != 404:
            return False, github_error(exc)
    try:
        repo.create_file(EC2_DEPLOY_WORKFLOW_PATH, "Add managed AWS EC2 deployment workflow", content, branch=branch)
        return True, "AWS EC2 deployment workflow added."
    except Exception as exc:
        return False, f"Failed to add workflow file: {github_error(exc)}"

def _download_ec2_artifact(artifact: Dict[str, Any]) -> Optional[str]:
    url = artifact.get("archive_download_url")
    if not url:
        return None
    response = _github_rest("GET", url)
    if response.status_code != 200:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            for name in z.namelist():
                if name.endswith("deployment-info.txt"):
                    return z.read(name).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, OSError):
        return None
    return None

def _parse_deployment_info(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result

def _new_aws_setup_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex

def _aws_setup_status_url(token: str) -> str:
    base = _ENV_AWS_SETUP_CALLBACK_URL
    if base.endswith("/callback"):
        base = base[:-len("/callback")]
    return f"{base}/status/{quote(token, safe='')}"

def _poll_aws_setup_callback() -> bool:
    token = str(st.session_state.get("aws_setup_token") or "").strip()
    if not token or not _ENV_AWS_SETUP_CALLBACK_URL:
        return False
    try:
        r = requests.get(_aws_setup_status_url(token), timeout=4)
        if not r.ok:
            return False
        data = r.json()
        role = aws_role_arn_from_input(str(data.get("role_arn") or ""))
        if data.get("status") == "connected" and role:
            st.session_state.aws_oidc_role_arn = role
            st.session_state.aws_account_id_input = str(data.get("account_id") or "")
            st.session_state.aws_region = str(data.get("region") or st.session_state.get("aws_region") or "us-east-1")
            st.session_state.aws_oidc_connected = True
            st.session_state.aws_setup_pending = False
            st.session_state.aws_setup_token = ""
            return True
    except Exception:
        pass
    return False

def _render_aws_callback_watcher() -> None:
    if not _ENV_AWS_SETUP_CALLBACK_URL or not st.session_state.get("aws_setup_token"):
        return
    fragment = getattr(st, "fragment", None)
    if fragment:
        @fragment(run_every="4s")
        def _watch():
            if _poll_aws_setup_callback():
                st.rerun()
            else:
                st.caption("⏳ Waiting for AWS setup to finish… the Agent will connect automatically.")
        _watch()
    else:
        st.caption("⏳ AWS setup is pending. Refresh this page after CloudFormation finishes.")

def _aws_oidc_role_arn() -> str:
    return str(st.session_state.get("aws_oidc_role_arn", "") or "").strip()


def _aws_oidc_ready() -> bool:
    return bool(
        st.session_state.get("gh_user")
        and st.session_state.get("github_access_token")
        and valid_aws_role_arn(_aws_oidc_role_arn())
    )


def aws_connection_status() -> Dict[str, Any]:
    role = _aws_oidc_role_arn()
    if not role:
        return {"ready": False, "message": "AWS is not connected. Use the secure GitHub OIDC setup."}
    if not valid_aws_role_arn(role):
        return {"ready": False, "message": "AWS role ARN format is invalid."}
    if not st.session_state.get("gh_user"):
        return {"ready": False, "message": "Connect GitHub first."}
    return {
        "ready": True,
        "role_arn": role,
        "message": "Connected through GitHub Actions OIDC. No AWS access key or secret is stored."
    }


def gh_deploy_aws_ec2(repo_name: str, runner_label: str = "ec2-deployer", port: str = "", ref: str = "") -> str:
    """Deploy through GitHub Actions OIDC so the connected user's AWS account is used."""
    if not _aws_oidc_ready():
        return "AWS is not connected yet. Say **connect AWS** and I'll guide you through the one-time passwordless setup."
    full_name = resolve_repo_name(repo_name)
    repo = st.session_state.gh_client.get_repo(full_name)
    branch = ref.strip() or repo.default_branch
    try:
        ensure_aws_oidc_workflow(repo, _aws_oidc_role_arn(), branch)
        ok, logs = aws_oidc_dispatch_and_wait(
            full_name, AWS_OIDC_WORKFLOW_PATH, branch,
            st.session_state.get("github_access_token", ""),
            {
                "operation": "deploy",
                "instance_id": st.session_state.get("aws_ec2_instance_id", ""),
                "region": st.session_state.get("aws_region", "us-east-1"),
                "port": port or "",
                "command": "",
                "add_gb": "10",
            },
            timeout=900,
        )
        url_match = re.search(r"AGENT_LIVE_URL=(https?://\S+)", logs)
        instance_match = re.search(r"(?:INSTANCE_ID|EC2_INSTANCE_ID)=([^\s]+)", logs)
        host_match = re.search(r"(?:PUBLIC_HOST|HOST)=([^\s]+)", logs)
        port_match = re.search(r"(?:ACTUAL_PORT|PORT)=(\d+)", logs)
        if ok:
            url = url_match.group(1) if url_match else ""
            instance_id = instance_match.group(1) if instance_match else (st.session_state.get("aws_ec2_instance_id", "") or "auto-detected")
            host = host_match.group(1) if host_match else ""
            actual_port = port_match.group(1) if port_match else ""
            set_live_view("aws_ec2", {
                "repository": full_name,
                "instance_id": instance_id,
                "host": host,
                "port": actual_port,
                "url": url,
                "deployment_mode": "GitHub Actions OIDC + AWS SSM",
            })
            return (
                "## 🚀 AWS deployment complete\n\n"
                + f"**Repository:** `{full_name}`\n\n"
                + f"**EC2 Instance ID:** `{instance_id}`\n\n"
                + (f"**Host:** `{host}`\n\n" if host else "")
                + (f"**Port:** `{actual_port}`\n\n" if actual_port else "")
                + (f"### 🔗 Live URL\n\n**{url}**\n\n[🌐 Open application]({url})\n\n" if url else "")
                + "The deployment ran in **the connected user's AWS account** using a short-lived OIDC credential.\n\n"
                + "```text\n" + logs[-6000:] + "\n```"
            )
        return "❌ AWS deployment failed.\n\n```text\n" + logs[-8000:] + "\n```"
    except Exception as exc:
        return f"AWS OIDC deployment failed: {exc}"


# --- AWS EC2 self-service diagnostics / permission auto-fix -----------------
# These let the Agent check and fix common EC2 problems (like a full disk)
# itself over SSM instead of telling the user to SSH in and run commands by
# hand, and let it recover from a missing IAM permission by asking a plain
# yes/no question instead of just failing.

_AWS_ACCESS_DENIED_RE = re.compile(
    r"not authorized to perform:\s*([A-Za-z0-9]+:[A-Za-z0-9_\*]+)", re.IGNORECASE
)


def _aws_extract_missing_permission(exc: Exception) -> Optional[str]:
    """Pull the exact missing IAM action (e.g. 'ssm:SendCommand') out of a
    boto3 AccessDenied/UnauthorizedAccess error message, if present."""
    message = str(exc)
    match = _AWS_ACCESS_DENIED_RE.search(message)
    return match.group(1) if match else None


def gh_aws_check_space(instance_id_override: str = "") -> str:
    if not _aws_oidc_ready():
        return "AWS is not connected. Say **connect AWS** first."
    repo_name = st.session_state.get("current_repo") or st.session_state.get("last_pushed_repo")
    if not repo_name:
        return "Push or select a GitHub repository first."
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    branch = repo.default_branch
    ensure_aws_oidc_workflow(repo, _aws_oidc_role_arn(), branch)
    ok, logs = aws_oidc_dispatch_and_wait(
        resolve_repo_name(repo_name), AWS_OIDC_WORKFLOW_PATH, branch,
        st.session_state.get("github_access_token",""),
        {"operation":"check_space","instance_id":instance_id_override or st.session_state.get("aws_ec2_instance_id",""),
         "region":st.session_state.get("aws_region","us-east-1"),"port":"","command":"","add_gb":"10"},
        timeout=300)
    return ("### 🖥️ AWS EC2 space check\n\n```text\n"+logs[-10000:]+"\n```") if ok else ("❌ AWS space check failed.\n\n```text\n"+logs[-10000:]+"\n```")



def gh_aws_free_space(instance_id_override: str = "") -> str:
    if not _aws_oidc_ready(): return "AWS is not connected. Say **connect AWS** first."
    repo_name = st.session_state.get("current_repo") or st.session_state.get("last_pushed_repo")
    if not repo_name: return "Push or select a GitHub repository first."
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name)); branch=repo.default_branch
    ensure_aws_oidc_workflow(repo, _aws_oidc_role_arn(), branch)
    ok, logs = aws_oidc_dispatch_and_wait(
        resolve_repo_name(repo_name), AWS_OIDC_WORKFLOW_PATH, branch,
        st.session_state.get("github_access_token",""),
        {"operation":"free_space","instance_id":instance_id_override or st.session_state.get("aws_ec2_instance_id",""),
         "region":st.session_state.get("aws_region","us-east-1"),"port":"","command":"","add_gb":"10"},
        timeout=300)
    return ("### 🧹 AWS space cleanup complete\n\n```text\n"+logs[-10000:]+"\n```") if ok else ("❌ AWS space cleanup failed.\n\n```text\n"+logs[-10000:]+"\n```")



def gh_aws_run_command(command: str, reason: str = "") -> str:
    if not _aws_oidc_ready(): return "AWS is not connected. Say **connect AWS** first."
    if not command.strip(): return "No command was given."
    repo_name = st.session_state.get("current_repo") or st.session_state.get("last_pushed_repo")
    if not repo_name: return "Push or select a GitHub repository first."
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name)); branch=repo.default_branch
    ensure_aws_oidc_workflow(repo, _aws_oidc_role_arn(), branch)
    ok, logs = aws_oidc_dispatch_and_wait(
        resolve_repo_name(repo_name), AWS_OIDC_WORKFLOW_PATH, branch,
        st.session_state.get("github_access_token",""),
        {"operation":"run_command","instance_id":st.session_state.get("aws_ec2_instance_id",""),
         "region":st.session_state.get("aws_region","us-east-1"),"port":"","command":command,"add_gb":"10"},
        timeout=600)
    return ("### ✅ AWS command completed\n\n```text\n"+logs[-10000:]+"\n```") if ok else ("❌ AWS command failed.\n\n```text\n"+logs[-10000:]+"\n```")



def gh_aws_grant_permission(action: Dict[str, Any]) -> str:
    return (
        "I won't modify IAM permissions automatically. AWS access is provided by the "
        "dedicated GitHub OIDC deployment role, and its permissions are intentionally "
        "limited to the Agent's deployment/EC2/SSM operations."
    )



def gh_aws_resize_volume(add_gb: int = 0, target_gb: int = 0) -> str:
    if not _aws_oidc_ready(): return "AWS is not connected. Say **connect AWS** first."
    repo_name = st.session_state.get("current_repo") or st.session_state.get("last_pushed_repo")
    if not repo_name: return "Push or select a GitHub repository first."
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name)); branch=repo.default_branch
    ensure_aws_oidc_workflow(repo, _aws_oidc_role_arn(), branch)
    amount = max(1, int(add_gb or 10))
    ok, logs = aws_oidc_dispatch_and_wait(
        resolve_repo_name(repo_name), AWS_OIDC_WORKFLOW_PATH, branch,
        st.session_state.get("github_access_token",""),
        {"operation":"resize_volume","instance_id":st.session_state.get("aws_ec2_instance_id",""),
         "region":st.session_state.get("aws_region","us-east-1"),"port":"","command":"","add_gb":str(amount)},
        timeout=600)
    return ("### 💾 AWS disk resize requested\n\n```text\n"+logs[-10000:]+"\n```") if ok else ("❌ AWS disk resize failed.\n\n```text\n"+logs[-10000:]+"\n```")



def gh_deploy_aws(
    repo_name: str,
    workflow: str = "",
    ref: str = "",
    inputs: Optional[Dict[str, Any]] = None,
) -> str:
    """Compatibility wrapper: all AWS deployment actions use the EC2 engine.

    A repo-specific deploy workflow is not required; the Agent creates/updates
    its standard workflow automatically before dispatching it.
    """
    inputs = inputs if isinstance(inputs, dict) else {}
    return gh_deploy_aws_ec2(
        repo_name,
        port=str(inputs.get("port", "") or ""),
        ref=ref,
    )

def find_streamlit_entrypoints(repo: Any, branch: str) -> List[str]:
    found: List[str] = []

    def walk(path: str = "", depth: int = 0) -> None:
        if depth > 3:
            return
        try:
            entries = repo.get_contents(path, ref=branch)
        except GithubException:
            return
        if not isinstance(entries, list):
            return
        for entry in entries:
            if entry.type == "file" and entry.name.endswith(".py"):
                found.append(entry.path)
            elif entry.type == "dir" and not entry.name.startswith("."):
                walk(entry.path, depth + 1)

    walk()
    return found


def gh_prepare_streamlit_deploy(
    repo_name: str,
    branch: str = "",
    app_path: str = "",
) -> str:
    full_name = resolve_repo_name(repo_name)
    repo = st.session_state.gh_client.get_repo(full_name)
    selected_branch = branch or repo.default_branch
    selected_path = app_path.strip().strip("/")
    if selected_path:
        candidate = repo.get_contents(selected_path, ref=selected_branch)
        if isinstance(candidate, list) or not selected_path.endswith(".py"):
            return f"`{selected_path}` is not a valid Streamlit Python entrypoint."
    else:
        candidates = find_streamlit_entrypoints(repo, selected_branch)
        preferred = ["app.py", "streamlit_app.py", "main.py"]
        selected_path = next(
            (
                path
                for name in preferred
                for path in candidates
                if path.rsplit("/", 1)[-1] == name
            ),
            sorted(candidates, key=lambda path: (path.count("/"), path))[0]
            if candidates
            else "",
        )
        if not selected_path:
            return "No Streamlit `.py` entrypoint found in the repository."

    deploy_url = (
        "https://share.streamlit.io/deploy"
        f"?repository={quote(full_name)}"
        f"&branch={quote(selected_branch)}"
        f"&mainModule={quote(selected_path)}"
    )
    return (
        "Streamlit Community Cloud deployment setup is ready.\n\n"
        f"- Repository: `{full_name}`\n"
        f"- Branch: `{selected_branch}`\n"
        f"- App file: `{selected_path}`\n\n"
        f"[Open Streamlit deployment setup]({deploy_url})\n\n"
        "You'll need to sign in on Cloud and confirm **Create app**. "
        "For a private repo, the Streamlit account needs to be given repo access."
    )


def extract_deployment_platform(text: str) -> Optional[str]:
    """Return a supported platform when the user explicitly names one."""
    value = text.lower().strip()
    for alias, platform in sorted(
        DEPLOYMENT_PLATFORM_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", value):
            return platform
    match = re.fullmatch(r"\s*([1-5])(?:\s*[\).:-])?\s*", value)
    if match:
        return DEPLOYMENT_PLATFORM_OPTIONS[int(match.group(1)) - 1]
    return None


def deployment_action_for(platform: str, repo_name: str) -> Dict[str, Any]:
    if platform == "Streamlit Cloud":
        return {
            "type": "deploy_streamlit",
            "repo": repo_name,
            "branch": "",
            "app_path": "",
            "platform": platform,
        }
    if platform == "AWS EC2":
        return {
            "type": "deploy_aws_ec2",
            "repo": repo_name,
            "port": "",
            "instance_id": st.session_state.get("aws_ec2_instance_id", ""),
            "ref": "",
            "platform": platform,
        }
    if platform == "Render" and _render_api_key():
        return {
            "type": "deploy_render",
            "repo": repo_name,
            "ref": "",
            "platform": platform,
        }
    return {
        "type": "prepare_deployment",
        "repo": repo_name,
        "platform": platform,
        "branch": "",
    }


def deployment_dashboard_url(
    platform: str,
    repo_url: str = "",
    branch: str = "",
) -> str:
    """Build a direct, repo-pre-filled one-click deploy link where the
    platform supports one, falling back to the closest generic setup page
    when it doesn't.
    """
    if platform == "Render" and repo_url:
        url = f"https://render.com/deploy?repo={quote(repo_url, safe='')}"
        if branch:
            url += f"&branch={quote(branch)}"
        return url
    if platform == "Google Cloud" and repo_url:
        return f"https://deploy.cloud.run/?git_repo={quote(repo_url, safe='')}"
    if platform == "Azure":
        return "https://portal.azure.com/#create/Microsoft.WebApp"
    return {
        "Azure": "https://portal.azure.com/#create/Microsoft.WebApp",
        "Google Cloud": "https://console.cloud.google.com/run",
        "Render": "https://dashboard.render.com/blueprints/new",
    }.get(platform, "")


def gh_prepare_platform_deploy(
    repo_name: str,
    platform: str,
    branch: str = "",
) -> str:
    """Prepare a deployment plan for platforms without a direct API integration."""
    if platform not in DEPLOYMENT_PLATFORM_OPTIONS:
        return "Unsupported deployment platform."
    repo = st.session_state.gh_client.get_repo(resolve_repo_name(repo_name))
    selected_branch = branch or repo.default_branch
    contents = repo.get_contents("", ref=selected_branch)
    names = (
        [item.path for item in contents]
        if isinstance(contents, list)
        else [contents.path]
    )
    workflows = list(repo.get_workflows())
    workflow_terms = {
        "Azure": ("azure",),
        "Google Cloud": ("google", "gcp", "cloud-run", "cloud run"),
        "Render": ("render",),
    }
    matching_workflow = next(
        (
            workflow
            for workflow in workflows
            if any(
                term in f"{workflow.name} {workflow.path}".lower()
                for term in workflow_terms.get(platform, ())
            )
            and "deploy" in f"{workflow.name} {workflow.path}".lower()
        ),
        None,
    )
    analysis = st.session_state.project_analysis or {}
    recommended = analysis.get("recommended_platform")
    reason = analysis.get("recommendation_reason")
    dashboard_url = deployment_dashboard_url(platform, repo.html_url, selected_branch)
    lines = [
        f"**{platform} deployment plan is ready.**",
        f"- Repository: `{repo.full_name}`",
        f"- Branch: `{selected_branch}`",
        f"- Detected top-level files: `{', '.join(names[:12]) or 'none'}`",
    ]
    if recommended and recommended != platform:
        lines.append(
            f"- File analysis recommendation: **{recommended}** — {reason}"
        )
    if matching_workflow:
        matching_workflow.create_dispatch(ref=selected_branch, inputs={})
        lines.extend(
            [
                f"- Matching workflow: `{matching_workflow.name}`",
                f"- Workflow triggered. Status: {repo.html_url}/actions",
            ]
        )
        return "\n".join(lines)

    lines.append(
        "- No matching GitHub Actions workflow found, so the deployment was not completed."
    )

    if platform == "Render":
        lines.extend(
            [
                "- This is a direct one-click deploy link: the repository is "
                "pre-filled, click **Deploy** on Render and your live "
                "`*.onrender.com` URL is ready right after the build finishes.",
                f"[Deploy {repo.full_name} on Render]({dashboard_url})",
            ]
        )
    elif platform == "Google Cloud":
        lines.extend(
            [
                "- This is a direct one-click deploy link: Cloud Run builds "
                "straight from this repo's `Dockerfile` and gives you a live "
                "`*.run.app` URL once the build finishes.",
                f"[Deploy {repo.full_name} on Google Cloud Run]({dashboard_url})",
            ]
        )
    else:  # Azure
        lines.extend(
            [
                "- Azure only offers a true one-click deploy button when the "
                "repo includes an ARM/Bicep template (`azuredeploy.json`), "
                "which this repo doesn't have — so there's no way to skip "
                "straight to a live URL here.",
                "- Next step: create an Azure Web App, connect this repo as "
                "the GitHub deployment source, and verify the startup command "
                "and dependency file.",
                f"[Open Azure Web App setup]({dashboard_url})",
            ]
        )
    return "\n".join(lines)


RENDER_API_BASE = "https://api.render.com/v1"
RENDER_DEPLOY_DONE_STATUSES = {
    "live",
    "build_failed",
    "update_failed",
    "canceled",
    "deactivated",
    "pre_deploy_failed",
}


def _render_api_key() -> str:
    return st.session_state.get("render_api_key", "").strip()


def _render_headers() -> Dict[str, str]:
    key = _render_api_key()
    if not key:
        raise RuntimeError(
            "No Render API key configured. Open **Deployment Platform → Render** "
            "in the sidebar and paste an API key from Render → Account Settings → API Keys."
        )
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def render_connection_status() -> Dict[str, Any]:
    """Return a safe, non-secret Render readiness snapshot for the UI."""
    if not _render_api_key():
        return {"ready": False, "message": "No Render API key set yet."}
    try:
        response = requests.get(
            f"{RENDER_API_BASE}/services", headers=_render_headers(), params={"limit": 1}, timeout=20
        )
        if response.status_code == 401:
            return {"ready": False, "message": "Render API key was rejected (401 Unauthorized)."}
        response.raise_for_status()
        return {"ready": True, "message": "Render API key verified."}
    except Exception as exc:
        return {"ready": False, "message": str(exc)[:1000]}


def render_find_service_for_repo(full_name: str) -> Optional[Dict[str, Any]]:
    """Look up an existing Render service already linked to this GitHub repo."""
    repo_url_candidates = {
        f"https://github.com/{full_name}",
        f"https://github.com/{full_name}.git",
    }
    cursor = ""
    for _ in range(10):
        params: Dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        response = requests.get(f"{RENDER_API_BASE}/services", headers=_render_headers(), params=params, timeout=20)
        response.raise_for_status()
        page = response.json()
        if not page:
            break
        for item in page:
            service = item.get("service", item)
            repo = str(service.get("repo", "")).rstrip("/")
            if repo.lower() in {url.lower() for url in repo_url_candidates}:
                return service
        if len(page) < 100:
            break
        cursor = page[-1].get("cursor", "")
        if not cursor:
            break
    return None


def render_wait_for_deploy(service_id: str, deploy_id: str, timeout: int = 900) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    poll_interval = 2.0
    while time.time() < deadline:
        try:
            response = requests.get(
                f"{RENDER_API_BASE}/services/{service_id}/deploys/{deploy_id}",
                headers=_render_headers(),
                timeout=20,
            )
            response.raise_for_status()
            last = response.json()
        except Exception:
            time.sleep(2.0)
            continue
        if str(last.get("status", "")) in RENDER_DEPLOY_DONE_STATUSES:
            return last
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.3, 6)
    return last or {"status": "timed_out"}


def gh_deploy_render_live(repo_name: str, ref: str = "") -> str:
    """Deploy directly through the Render API and return the actual live URL."""
    full_name = resolve_repo_name(repo_name)
    repo = st.session_state.gh_client.get_repo(full_name)
    branch = ref.strip() or repo.default_branch

    service = render_find_service_for_repo(full_name)
    if not service:
        one_click_url = deployment_dashboard_url("Render", repo.html_url, branch)
        return (
            f"No Render service is linked to `{full_name}` yet on this Render account, "
            "and the Render API can only manage services it can already see — it can't "
            "silently create one on a repo it has never been authorized for.\n\n"
            "**One-time step:** open the link below, confirm **Deploy**, and Render will "
            "create the service and pick up `render.yaml` from the repo automatically.\n\n"
            f"[Deploy {full_name} on Render]({one_click_url})\n\n"
            "After that first time, just ask to deploy on Render again and the Agent will "
            "trigger the redeploy through the Render API directly and hand back the live URL "
            "here in chat — no need to go into the Render dashboard."
        )

    service_id = str(service.get("id", ""))
    service_name = str(service.get("name", full_name))
    dashboard_url = str(service.get("dashboardUrl", ""))
    emit_action_event(
        "step_started", "running", "Triggering Render deploy",
        f"Redeploying `{service_name}` from `{full_name}` (branch `{branch}`).",
        {"service_id": service_id}, step=1, total_steps=3,
    )
    try:
        response = requests.post(
            f"{RENDER_API_BASE}/services/{service_id}/deploys",
            headers=_render_headers(),
            json={"clearCache": "do_not_clear"},
            timeout=30,
        )
        response.raise_for_status()
    except Exception as exc:
        return f"Could not start the Render deploy: {exc}"
    deploy = response.json()
    deploy_id = str(deploy.get("id", ""))
    emit_action_event(
        "step_started", "running", "Building on Render",
        "Waiting for Render to build and start the service.",
        {"deploy_id": deploy_id}, step=2, total_steps=3,
    )
    result = render_wait_for_deploy(service_id, deploy_id, timeout=900)
    status = str(result.get("status", ""))
    if status != "live":
        emit_action_event(
            "step_failed", "failed", "Render deployment failed",
            f"Deploy status: {status}", {"deploy_id": deploy_id, "status": status},
            step=2, total_steps=3,
        )
        return (
            f"❌ Render deployment did not go live (status: `{status}`).\n\n"
            f"[Open Render logs for this deploy]({dashboard_url})"
        )
    try:
        response = requests.get(f"{RENDER_API_BASE}/services/{service_id}", headers=_render_headers(), timeout=20)
        response.raise_for_status()
        service = response.json()
    except Exception:
        pass
    live_url = str(service.get("serviceDetails", {}).get("url", "") or "")
    set_live_view("render", {
        "repository": full_name, "service_id": service_id, "service_name": service_name,
        "branch": branch, "url": live_url, "dashboard_url": dashboard_url,
    })
    emit_action_event(
        "step_completed", "completed", "Render deployment complete",
        f"Live app: {live_url or 'URL pending'}", {"url": live_url, "service_id": service_id},
        step=3, total_steps=3,
    )
    return ("## 🚀 Render deployment complete\n\n"
            "### 🔗 Direct Live URL\n\n"
            f"**{live_url}**\n\n"
            f"[🌐 Open deployed application]({live_url})\n\n"
            f"- **Service:** `{service_name}`\n"
            f"- **Repository:** `{full_name}`\n"
            f"- **Branch:** `{branch}`\n"
            f"[Render dashboard / logs]({dashboard_url})\n\n"
            "✅ Deploy status came back `live` from the Render API and the URL above is the "
            "actual `serviceDetails.url` for this service — not a guessed link.")


def requires_github(action_type: str) -> bool:
    # Every action except a purely-local project-analysis read and the
    # AWS permission-explainer (which never touches GitHub or the repo)
    # needs a connected GitHub client - including every AWS action here,
    # since AWS deployment/diagnostics work by installing a workflow file
    # into and dispatching it through the user's own GitHub repository.
    return action_type not in {"analyze_project", "aws_grant_permission"}


AWS_ACTION_TYPES = {
    "deploy_aws_ec2",
    "aws_ec2_check_space",
    "aws_ec2_free_space",
    "aws_ec2_resize_volume",
    "aws_ec2_run_command",
}


def execute_action(action: Dict[str, Any]) -> str:
    action_type = action.get("type")
    st.session_state.active_request_id = st.session_state.get("active_request_id") or uuid.uuid4().hex
    st.session_state.active_action_id = uuid.uuid4().hex
    emit_action_event("step_started", "running", "Action started",
                      f"Executing {action_type}.", action)
    if requires_github(action_type) and st.session_state.gh_user is None:
        return "Connect your GitHub token from the sidebar first."
    # Hard guard, independent of anything the AI planner decided: no AWS
    # action runs unless *this session* has connected its own AWS account
    # via the passwordless GitHub-OIDC role. There is no code path left in
    # this app that can reach AWS with anyone's long-lived keys or with the
    # developer's/host's own credentials — every AWS call happens inside a
    # GitHub Actions run in the connecting user's own repo, using a
    # short-lived credential scoped to the role *they* connected.
    if action_type in AWS_ACTION_TYPES and not _aws_oidc_ready():
        return (
            "AWS isn't connected for this session yet, so I can't touch any AWS "
            "resource — I only ever deploy into the AWS account you connect "
            "yourself, never the developer's. Open **Deployment Platform → AWS EC2** "
            "in the sidebar (or type \"connect aws\") and I'll walk you through the "
            "one-time, no-keys-needed CloudFormation setup — then say deploy again."
        )
    try:
        if action_type == "list_repos":
            return gh_list_repos()
        if action_type == "analyze_project":
            return project_analysis_text(st.session_state.project_analysis)
        if action_type == "open_repo":
            return gh_open_repo(str(action.get("repo", "")))
        if action_type == "create_repo":
            return gh_create_repo(
                str(action.get("name", "")),
                bool(action.get("private", False)),
                str(action.get("description", "")),
                str(action.get("owner", "")),
            )
        if action_type == "rename_repo":
            return gh_rename_repo(
                str(action.get("repo", "")),
                str(action.get("new_name", "")),
            )
        if action_type == "create_issue":
            return gh_create_issue(
                str(action.get("repo", "")),
                str(action.get("title", "")),
                str(action.get("body", "")),
            )
        if action_type == "update_issue":
            return gh_update_issue(
                str(action.get("repo", "")),
                int(action.get("number", 0)),
                str(action.get("title", "")),
                str(action.get("body", "")),
                str(action.get("state", "")),
            )
        if action_type == "close_pull_request":
            return gh_close_pull_request(
                str(action.get("repo", "")),
                int(action.get("number", 0)),
            )
        if action_type == "list_issues":
            return gh_list_issues(str(action.get("repo", "")))
        if action_type == "read_file":
            return gh_read_file(str(action.get("repo", "")), str(action.get("path", "")))
        if action_type == "update_file":
            return gh_update_file(
                str(action.get("repo", "")),
                str(action.get("path", "")),
                str(action.get("content", "")),
                str(action.get("commit_message", "Update file via GitHub Agent")),
            )
        if action_type == "delete_file":
            return gh_delete_file(
                str(action.get("repo", "")),
                str(action.get("path", "")),
                str(action.get("commit_message", "Delete file via GitHub Agent")),
            )
        if action_type == "create_branch":
            return gh_create_branch(
                str(action.get("repo", "")),
                str(action.get("branch", "")),
                str(action.get("from_branch", "")),
            )
        if action_type == "create_pull_request":
            return gh_create_pull_request(
                str(action.get("repo", "")),
                str(action.get("title", "")),
                str(action.get("head", "")),
                str(action.get("base", "")),
                str(action.get("body", "")),
            )
        if action_type == "merge_pull_request":
            return gh_merge_pull_request(
                str(action.get("repo", "")),
                int(action.get("number", 0)),
                str(action.get("commit_message", "")),
            )
        if action_type == "close_issue":
            return gh_close_issue(
                str(action.get("repo", "")),
                int(action.get("number", 0)),
            )
        if action_type == "delete_repo":
            return gh_delete_repo(str(action.get("repo", "")))
        if action_type == "list_releases":
            return gh_list_releases(str(action.get("repo", "")))
        if action_type == "create_release":
            return gh_create_release(
                str(action.get("repo", "")),
                str(action.get("tag", "")),
                str(action.get("name", "")),
                str(action.get("body", "")),
                bool(action.get("draft", False)),
                bool(action.get("prerelease", False)),
            )
        if action_type == "delete_release":
            return gh_delete_release(
                str(action.get("repo", "")),
                int(action.get("release_id", 0)),
            )
        if action_type == "list_workflows":
            return gh_list_workflows(str(action.get("repo", "")))
        if action_type == "repo_settings":
            return gh_repo_settings(str(action.get("repo", "")))
        if action_type == "update_repo_settings":
            changes = action.get("changes", {})
            return gh_update_repo_settings(
                str(action.get("repo", "")),
                changes if isinstance(changes, dict) else {},
            )
        if action_type == "run_workflow":
            inputs = action.get("inputs", {})
            return gh_run_workflow(
                str(action.get("repo", "")),
                str(action.get("workflow", "")),
                str(action.get("ref", "")),
                inputs if isinstance(inputs, dict) else {},
            )
        if action_type == "deploy_aws":
            inputs = action.get("inputs", {})
            return gh_deploy_aws(
                str(action.get("repo", "")),
                str(action.get("workflow", "")),
                str(action.get("ref", "")),
                inputs if isinstance(inputs, dict) else {},
            )
        if action_type == "deploy_aws_ec2":
            return gh_deploy_aws_ec2(
                str(action.get("repo", "")),
                str(action.get("runner_label", "ec2-deployer")),
                str(action.get("port", "")),
                str(action.get("ref", "")),
            )
        if action_type == "push_project":
            return gh_push_project(
                str(action.get("repo_name", "")),
                bool(action.get("private", st.session_state.private_repo)),
                str(action.get("commit_message", st.session_state.commit_message)),
            )
        if action_type == "update_files_subset":
            files = action.get("files", [])
            return gh_update_files_subset(
                str(action.get("repo_name", "")),
                files if isinstance(files, list) else [],
                str(action.get("commit_message", st.session_state.commit_message)),
            )
        if action_type == "deploy_streamlit":
            return gh_prepare_streamlit_deploy(
                str(action.get("repo", "")),
                str(action.get("branch", "")),
                str(action.get("app_path", "")),
            )
        if action_type == "prepare_deployment":
            return gh_prepare_platform_deploy(
                str(action.get("repo", "")),
                str(action.get("platform", "")),
                str(action.get("branch", "")),
            )
        if action_type == "deploy_render":
            return gh_deploy_render_live(
                str(action.get("repo", "")),
                str(action.get("ref", "")),
            )
        if action_type == "aws_ec2_check_space":
            return gh_aws_check_space()
        if action_type == "aws_ec2_free_space":
            return gh_aws_free_space()
        if action_type == "aws_ec2_resize_volume":
            return gh_aws_resize_volume(
                int(action.get("add_gb", 0) or 0),
                int(action.get("target_gb", 0) or 0),
            )
        if action_type == "aws_ec2_run_command":
            return gh_aws_run_command(
                str(action.get("command", "")),
                str(action.get("reason", "")),
            )
        if action_type == "aws_grant_permission":
            return gh_aws_grant_permission(action)
        return "No supported GitHub action found for this request."
    except Exception as exc:
        message = github_error(exc) if isinstance(exc, GithubException) else str(exc)
        is_aws_action = action_type in {
            "deploy_aws", "deploy_aws_ec2", "aws_ec2_check_space",
            "aws_ec2_free_space", "aws_ec2_resize_volume", "aws_ec2_run_command",
        }
        missing_permission = _aws_extract_missing_permission(exc) if is_aws_action else None
        if missing_permission and action_type != "aws_grant_permission":
            grant_action = {
                "type": "aws_grant_permission",
                "permission": missing_permission,
                "original_action": action,
            }
            st.session_state.pending_confirmation = grant_action
            return confirmation_preview(grant_action)
        if is_aws_action and ("unable to locate credentials" in message.lower() or "aws is not connected" in message.lower()):
            return (
                "AWS is not connected. Say **connect AWS** and I will guide you through "
                "the one-time passwordless OIDC setup. No AWS access key is required."
            )
        if action_type == "deploy_render" and "no render api key" in message.lower():
            return (
                "Render action failed: no Render API key configured. Open "
                "**Deployment Platform → Render** in the sidebar and paste a "
                "key from Render → Account Settings → API Keys, then try again."
            )
        label = "AWS action" if is_aws_action else ("Render action" if action_type == "deploy_render" else "GitHub action")
        return f"{label} failed: {message}"


CONFIRMATION_ACTIONS = {
    "create_repo",
    "rename_repo",
    "create_issue",
    "update_issue",
    "close_pull_request",
    "update_file",
    "delete_file",
    "create_branch",
    "create_pull_request",
    "merge_pull_request",
    "close_issue",
    "delete_repo",
    "create_release",
    "delete_release",
    "run_workflow",
    "deploy_aws",
    "deploy_aws_ec2",
    "push_project",
    "update_files_subset",
    "deploy_streamlit",
    "prepare_deployment",
    "deploy_render",
    "update_repo_settings",
    "aws_ec2_free_space",
    "aws_ec2_resize_volume",
    "aws_ec2_run_command",
    "aws_grant_permission",
}


def needs_confirmation(action: Dict[str, Any]) -> bool:
    return action.get("type") in CONFIRMATION_ACTIONS


def set_pending_confirmation(action: Dict[str, Any]) -> None:
    """Queue an action for yes/no approval AND remember which repo it's
    about. Several sidebar panels (most importantly the AWS EC2 connect
    panel) only render their real controls once
    st.session_state.current_repo is set - before this fix, asking to
    deploy a repo purely through chat (e.g. "ResumeScreening2 deploy kro
    aws") queued the confirmation but never told the sidebar which repo
    was chosen, so the AWS "Connect" UI stayed hidden behind the
    "select/push a repository first" message even though a repo had
    already been picked in chat."""
    st.session_state.pending_confirmation = action
    repo_ref = action.get("repo") or action.get("repo_name")
    if repo_ref:
        st.session_state.current_repo = repo_ref


def confirmation_preview(action: Dict[str, Any]) -> str:
    action_type = action.get("type")
    if action_type == "push_project":
        return (
            f"About to push the project into repository `{action.get('repo_name')}`.\n"
            f"- Visibility: {'Private' if action.get('private') else 'Public'}\n"
            f"- Commit message: `{action.get('commit_message')}`\n\n"
            "Type `yes` to commit, `no` to cancel."
        )
    if action_type == "update_files_subset":
        files = action.get("files", [])
        file_list = "\n".join(f"- `{item['path']}`" for item in files) if isinstance(files, list) else ""
        return (
            f"Only this attached "
            f"file{'s' if len(files) != 1 else ''} will be updated in repository `{action.get('repo_name')}` (the rest of the repo won't be touched):\n\n"
            f"{file_list}\n\n"
            f"- Commit message: `{action.get('commit_message')}`\n\n"
            "Type `yes` to commit, `no` to cancel."
        )
    if action_type == "deploy_streamlit":
        return (
            f"About to prepare `{action.get('repo')}` for Streamlit Community Cloud.\n"
            "Type `yes` to create the deployment setup link, `no` to cancel."
        )
    if action_type == "deploy_aws_ec2":
        return (
            f"About to deploy repository `{action.get('repo')}` to **AWS EC2**.\n"
            f"- EC2: `{action.get('instance_id') or os.getenv('EC2_INSTANCE_ID', 'auto-detect')}`\n"
            "- The GitHub repository will be cloned/pulled on EC2\n"
            "- A Docker image will be built on EC2\n"
            "- An available host port will be chosen automatically\n"
            "- The required security-group port will be added automatically\n"
            "- Any existing same-name container will be replaced\n"
            "- Once deployment completes, the **actual live URL + host + port** will be returned\n\n"
            "**Permission required:** AWS EC2 deployment + SSM command + security-group port update.\n"
            "Type `yes` to continue, `no` to cancel."
        )
    if action_type == "aws_ec2_free_space":
        return (
            "About to free up disk space on the EC2 instance:\n"
            "- Remove stopped containers, unused Docker images, and build cache\n"
            "- Currently running containers are left untouched\n\n"
            "Type `yes` to continue, `no` to cancel."
        )
    if action_type == "aws_ec2_resize_volume":
        target = action.get("target_gb") or 0
        add = action.get("add_gb") or (10 if not target else 0)
        size_desc = f"to **{target} GiB**" if target else f"by **+{add} GiB**"
        return (
            f"About to grow the EC2 instance's root EBS volume {size_desc} and "
            "extend the filesystem to use the new space.\n"
            "- This increases the volume's billed storage size on AWS (small ongoing cost increase)\n"
            "- EBS volumes can only grow, not shrink\n"
            "- The instance keeps running normally during the resize\n\n"
            "Type `yes` to continue, `no` to cancel."
        )
    if action_type == "aws_ec2_run_command":
        return (
            f"About to run this command on the EC2 instance via SSM:\n\n"
            f"```bash\n{action.get('command', '')}\n```\n"
            f"Reason: {action.get('reason', '—')}\n\n"
            "Type `yes` to run it, `no` to cancel."
        )
    if action_type == "aws_grant_permission":
        return (
            f"AWS says the configured keys are missing this permission:\n\n"
            f"**`{action.get('permission', 'unknown')}`**\n\n"
            "Do you want me to try adding just this permission to the AWS "
            "identity currently connected, so the original request can go "
            "through? Type `yes` to try, `no` to cancel.\n\n"
            "(If the connected keys also can't grant new permissions, I'll "
            "instead give you the exact policy snippet to add in the AWS "
            "console.)"
        )
    if action_type == "deploy_aws":
        return (
            f"About to trigger the AWS GitHub Actions deployment for repository "
            f"`{action.get('repo')}`. Type `yes` to run the workflow; `no` to cancel."
        )
    if action_type == "prepare_deployment":
        return (
            f"About to prepare a deployment plan for repository `{action.get('repo')}` "
            f"on **{action.get('platform')}**.\n"
            "If a matching workflow is found it will be triggered; otherwise you'll get setup instructions. "
            "Type `yes` to continue, `no` to cancel."
        )
    if action_type == "deploy_render":
        return (
            f"About to deploy repository `{action.get('repo')}` on **Render** "
            "through the Render API.\n"
            "- If a Render service is already linked to this repo, it will be "
            "redeployed and the **actual live URL** returned once the build finishes.\n"
            "- If no service is linked yet, you'll get the one-click Render setup link instead.\n\n"
            "Type `yes` to continue, `no` to cancel."
        )
    if action_type == "rename_repo":
        return (
            f"About to rename repository `{action.get('repo')}` "
            f"to `{action.get('new_name')}`.\n\n"
            "Type `yes` to rename it on GitHub, `no` to cancel."
        )
    if action_type == "update_file":
        content = str(action.get("content", ""))
        preview = content[:500] + ("..." if len(content) > 500 else "")
        return (
            f"About to update file `{action.get('path')}`.\n\n"
            f"```text\n{preview}\n```\n"
            "Type `yes` to commit this change, `no` to cancel."
        )
    if action_type == "delete_repo":
        target = action.get("repo", "")
        return (
            f"Warning: about to permanently delete repository `{target}`.\n"
            "If you really want to delete it, type `yes delete`; `no` to cancel."
        )
    return (
        f"About to perform action `{action_type}`. Type `yes` to confirm, "
        "`no` to cancel."
    )


def is_confirmation_yes(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(yes|y|haan|ha|ok|okay|confirm|proceed|kar do|yes delete)\s*",
            text,
            flags=re.IGNORECASE,
        )
    )


def is_confirmation_no(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(no|n|nahi|nahin|cancel|cancel karo|mat karo)\s*",
            text,
            flags=re.IGNORECASE,
        )
    )


def looks_like_push(text: str) -> bool:
    value = text.lower()
    push_words = (
        "push",
        "publish",
        "upload",
        "github pe",
        "github par",
        "github mein",
        "github me",
    )
    project_words = (
        "project",
        "folder",
        "file",
        "files",
        "repo",
        "repository",
        "project",
    )
    return any(word in value for word in push_words) and (
        any(word in value for word in project_words) or "github" in value
    )


def render_aws_connect_wizard_step() -> None:
    st.info(
        "AWS setup is passwordless. Use **Deployment Control Center → AWS EC2** in the sidebar. "
        "Download the generated CloudFormation setup, create it in your AWS account, then paste "
        "only the returned Role ARN. No AWS access key is requested."
    )



def looks_like_aws_connect_request(text: str) -> bool:
    value = text.lower()
    triggers = (
        "connect aws", "aws connect", "aws se connect", "aws ko connect",
        "aws jodo", "aws jode", "aws setup", "aws configure",
        "connect to aws", "aws account link", "aws account connect",
        "aws credential", "aws login", "aws sign in", "aws signin",
    )
    return any(t in value for t in triggers)


def aws_connect_guidance_reply() -> str:
    if _aws_oidc_ready():
        return (
            "AWS is already connected for this GitHub account ✅. "
            "Just say **deploy this project to AWS** and I'll handle it."
        )
    st.session_state.aws_chat_wizard_active = True
    st.session_state.deployment_platform = "AWS EC2"
    return (
        "Sure — let's connect your own AWS account securely. You will **not** paste an "
        "AWS Access Key or Secret Key. Use the AWS setup section in the sidebar, download "
        "the one-time CloudFormation template for your GitHub repo, create the stack while "
        "signed into your AWS account, then paste only the non-secret **Role ARN**. "
        "After that, I can deploy and manage the EC2 workload through short-lived OIDC credentials."
    )



def render_connect_wizard_step() -> None:
    """Draws the current Render-connect wizard step (sidebar or chat).
    Advances only when the user explicitly clicks a '✅ ... kar liya' confirm
    button for that exact step — same one-step-at-a-time, permission-gated
    pattern as the AWS wizard."""
    if "render_wizard_step" not in st.session_state:
        st.session_state.render_wizard_step = 0
    step = st.session_state.render_wizard_step
    total = 3
    st.caption(f"🧭 Render setup wizard — Step {min(step, total - 1) + 1} / {total}")
    st.progress((min(step, total - 1) + 1) / total)

    def _go(next_step: int):
        st.session_state.render_wizard_step = next_step
        st.rerun()

    if step == 0:
        st.markdown("**Step 1 — Do you have a Render account?**")
        st.caption(
            "If not, the signup page will open — sign up with GitHub so your "
            "repos are already accessible (email/OTP works too, but you'd "
            "connect GitHub separately afterwards)."
        )
        st.link_button("🆕 Create a new Render account / open sign up page", "https://dashboard.render.com/register", use_container_width=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Account created / already have one → Continue", use_container_width=True, type="primary", key="renderw_go1"):
                _go(1)
        with c2:
            st.caption("The next step will appear once your account is ready.")

    elif step == 1:
        st.markdown("**Step 2 — Sign in and connect GitHub**")
        st.caption(
            "Sign in, then under Account Settings → GitHub, connect your GitHub "
            "account and grant it access to the repo(s) you want to deploy — "
            "Render's API can only see/manage repos it has been authorized on."
        )
        st.link_button("1️⃣ Open Render Dashboard (sign in)", "https://dashboard.render.com/login", use_container_width=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Signed in & GitHub connected → Continue", use_container_width=True, type="primary", key="renderw_go2"):
                _go(2)
        with c2:
            if st.button("⬅️ Back", use_container_width=True, key="renderw_back1"):
                _go(0)

    elif step == 2:
        st.markdown("**Step 3 — Create an API Key and paste it straight into the chat box**")
        st.caption(
            "Account Settings → **API Keys** → **Create API Key** → give it any "
            "name (e.g. `github-agent`) → copy the key (shown only once, starts "
            "with `rnd_`)."
        )
        st.link_button("2️⃣ Open API Keys page", "https://dashboard.render.com/settings#api-keys", use_container_width=True)
        st.info(
            "⬇️ Now paste that key straight into the **message box** below in this "
            "chat (no separate field needed) — I'll auto-detect and verify it. "
            "It stays only in this browser session and is never saved or logged."
        )
        if st.button("⬅️ Back", use_container_width=True, key="renderw_back2"):
            _go(1)


def looks_like_render_connect_request(text: str) -> bool:
    """Detect 'connect Render' style requests so the chat never asks the user
    to paste a Render API key directly - that must always go through this
    guarded step-by-step wizard instead."""
    value = text.lower()
    triggers = (
        "connect render",
        "render connect",
        "render se connect",
        "render ko connect",
        "render jodo",
        "render jode",
        "render setup",
        "render configure",
        "connect to render",
        "render account link",
        "render account connect",
        "render api key kaise",
        "render key kaise",
        "render credential",
        "render login",
        "render sign in",
        "render signin",
    )
    return any(t in value for t in triggers)


def extract_render_api_key_from_text(text: str) -> Optional[str]:
    """Pull a Render API key (format `rnd_...`) out of a normal chat message,
    so the user can copy from the Render dashboard and paste straight into
    chat instead of needing a separate form field."""
    match = re.search(r"\brnd_[A-Za-z0-9]{20,}\b", text)
    return match.group(0) if match else None


def render_connect_guidance_reply() -> str:
    if _render_api_key() and st.session_state.get("render_verified_once"):
        return (
            "Render is already connected for this session ✅. Just say "
            "**\"Render pe deploy karo\"** (with the repo), and I'll redeploy "
            "and hand back the live URL."
        )
    st.session_state.render_chat_wizard_active = True
    st.session_state.render_wizard_step = 0
    st.session_state.deployment_platform = "Render"
    return (
        "Sure, let's connect Render — right here in chat, no need to go anywhere else. "
        "The first step is below: click its button to open that page, do what it says, "
        "then press **✅ ... done → Continue**. Each step only proceeds with your "
        "permission, and the last step just needs you to paste the API key — after "
        "that I'll handle deploys on Render myself, and give you the real live URL "
        "every time."
    )


def looks_like_deploy(text: str) -> bool:
    value = text.lower()
    deploy_words = (
        "deploy",
        "deply",
        "deploi",
        "diploy",
        "streamlit cloud",
        "streamlit par",
        "streamlit pe",
    )
    return any(word in value for word in deploy_words)


def looks_like_create_repo(text: str) -> bool:
    value = text.lower()
    return (
        any(word in value for word in ("create", "new", "make", "bana"))
        and bool(re.search(r"\b(repo|repository)\b", value))
        and not looks_like_push(value)
    )


def looks_like_rename_repo(text: str) -> bool:
    value = text.lower()
    rename_words = (
        "rename",
        "renamed",
        "renarte",
        "naam badal",
        "rename karo",
        "rename kar",
    )
    has_repo_word = bool(
        re.search(r"\b(repo|repos|repository|repositories)\b", value)
    )
    has_owner_repo_path = bool(
        re.search(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b", value)
    )
    natural_name_change = bool(
        re.search(
            r"\b(repo|repository)\b.*\b(ka naam|name)\b.*\b(rakh|rakho|badal)\b",
            value,
        )
    )
    return (
        (any(word in value for word in rename_words) and (has_repo_word or has_owner_repo_path))
        or natural_name_change
    )


def looks_like_delete_repo(text: str) -> bool:
    value = text.lower()
    return (
        any(word in value for word in ("delete", "remove", "erase"))
        and bool(re.search(r"\b(repo|repository)\b", value))
    )


def looks_like_list_repos(text: str) -> bool:
    value = text.lower()
    return bool(
        re.search(r"\b(list|show|display|mere|my)\b", value)
        and re.search(r"\b(repos?|repositories)\b", value)
    )


def looks_like_analyze(text: str) -> bool:
    value = text.lower()
    return any(
        phrase in value
        for phrase in (
            "analyze project",
            "analyse project",
            "project analysis",
            "framework detect",
            "framework batao",
            "project check",
            "analyze this",
            "analyse this",
        )
    )


GENERATE_PROJECT_VERBS = (
    "banao",
    "banavo",
    "banade",
    "bana do",
    "bana ke do",
    "banaiye",
    "banaye",
    "bana kar do",
    "banadena",
    "bana dena",
    "create",
    "generate",
    "build",
    "banao naa",
)
GENERATE_PROJECT_NOUNS = ("project", "app", "application", "website", "site")
GENERATE_PROJECT_FRAMEWORKS = ("streamlit",)


def looks_like_generate_project(text: str) -> bool:
    """Detect requests to build a brand-new project from scratch (not an upload)."""
    value = text.lower()
    if not any(framework in value for framework in GENERATE_PROJECT_FRAMEWORKS):
        return False
    if not any(noun in value for noun in GENERATE_PROJECT_NOUNS):
        return False
    if looks_like_push(value):
        return False
    return any(verb in value for verb in GENERATE_PROJECT_VERBS)


def extract_generate_project_framework(text: str) -> str:
    value = text.lower()
    for framework in GENERATE_PROJECT_FRAMEWORKS:
        if framework in value:
            return framework
    return "streamlit"


def looks_like_aws_deploy(text: str) -> bool:
    value = text.lower()
    return "aws" in value and looks_like_deploy(value)


def looks_like_ec2_deploy(text: str) -> bool:
    value = text.lower()
    return looks_like_deploy(value) and any(
        term in value for term in ("aws", "ec2", "amazon web services", "amazon server")
    )


def looks_like_open_repo(text: str) -> bool:
    value = text.lower()
    open_words = (
        "open",
        "show",
        "display",
        "view",
        "khol",
        "kholo",
        "dikhao",
    )
    has_repo_word = bool(re.search(r"\b(repo|repos|repository|repositories)\b", value))
    has_owner_repo_path = bool(re.search(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b", value))
    return any(word in value for word in open_words) and (has_repo_word or has_owner_repo_path)


def looks_like_settings(text: str) -> bool:
    value = text.lower()
    settings_words = ("setting", "settings", "config")
    action_words = (
        "open", "show", "go to", "goto", "jao", "khol", "kholo", "dikhao",
        "change", "update", "badal", "kar do", "karo", "on", "off",
        "private", "public",
    )
    return any(word in value for word in settings_words) and any(
        word in value for word in action_words
    )


SETTINGS_KEYWORD_PATTERN = re.compile(
    r"\b(settings|setting|config)\b", flags=re.IGNORECASE
)


def extract_settings_repo_name(text: str) -> Optional[str]:
    """Extract the repo name out of a settings command, ignoring the word
    'settings' itself so it is never mistaken for the repo name."""
    stripped = SETTINGS_KEYWORD_PATTERN.sub(" ", text)
    return extract_open_repo_name(stripped)


def extract_settings_changes(text: str) -> Dict[str, Any]:
    """Detect explicit settings changes from natural Hindi/Hinglish/English text."""
    value = text.lower()
    changes: Dict[str, Any] = {}

    if re.search(r"\bpublic\b", value) and re.search(r"\bpublic\s+(kar|karo|kar do)\b|\bmake\s+it\s+public\b|\bset\s+to\s+public\b", value):
        changes["private"] = False
    elif re.search(r"\bpublic\b", value) and not re.search(r"\bprivate\b", value) and any(
        w in value for w in ("kar do", "karo", "kar deejiye", "banao")
    ):
        changes["private"] = False
    if re.search(r"\bprivate\b", value) and any(
        w in value for w in ("kar do", "karo", "kar deejiye", "banao", "make it private", "set to private")
    ):
        changes["private"] = True

    for feature, key in (("issues", "has_issues"), ("wiki", "has_wiki"), ("projects", "has_projects")):
        off_match = re.search(rf"\b{feature}\b[^.]{{0,15}}\b(off|band|disable|hata)\b", value)
        on_match = re.search(rf"\b{feature}\b[^.]{{0,15}}\b(on|chalu|enable|shuru)\b", value)
        if off_match:
            changes[key] = False
        elif on_match:
            changes[key] = True

    description_match = re.search(
        r"description\s*(?:change|update|badal)?(?:\s+karke|\s+to|\s*[:=]\s*)?\s*[\"']?([^\"'\n]{3,150})",
        text,
        flags=re.IGNORECASE,
    )
    if description_match:
        changes["description"] = description_match.group(1).strip()

    branch_match = re.search(
        r"default\s+branch\s*(?:change|update|badal)?(?:\s+karke|\s+to|\s*[:=]\s*)?\s*[\"']?([A-Za-z0-9._/-]{1,80})",
        text,
        flags=re.IGNORECASE,
    )
    if branch_match:
        changes["default_branch"] = branch_match.group(1).strip("`'\" ")

    return changes


def extract_open_repo_name(text: str) -> Optional[str]:
    repo_pattern = r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)"
    explicit_path = re.search(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b", text)
    if explicit_path:
        return explicit_path.group(0)
    patterns = [
        rf"(?:open|show|display|view|deploy|delete|remove|erase|khol(?:o)?|dikhao)\s+(?:the\s+|my\s+|mera\s+|meri\s+|to\s+)?(?:repo(?:sitory)?\s+)?{repo_pattern}",
        rf"{repo_pattern}\s+(?:ka|ki)?\s*(?:repo|repository)\s+(?:open|show|display|view|khol)",
        rf"(?:repo|repository)\s+{repo_pattern}\s+(?:open|show|display|view|khol)",
    ]
    ignored = {
        "repo",
        "repos",
        "repository",
        "repositories",
        "kro",
        "please",
        "na",
        "this",
        "project",
        "app",
        "to",
        "aws",
        "streamlit",
        "cloud",
    }
    # A local filename (a ZIP upload, a source file, ...) is never itself a
    # GitHub repo name — deployment/opening always targets an actual repo
    # that has been pushed, not the raw uploaded file. Reject these so a
    # phrase like "deploy myproject.zip" doesn't get treated as a real repo.
    non_repo_extensions = (
        ".zip", ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".png",
        ".jpg", ".jpeg", ".exe", ".csv", ".pdf", ".h5", ".pkl", ".ipynb",
    )
    for pattern in patterns:
        match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip("`'\" ")
            if candidate.lower() in ignored:
                continue
            if candidate.lower().endswith(non_repo_extensions):
                continue
            return candidate
    return None


def extract_rename_details(text: str) -> Optional[Dict[str, str]]:
    """Extract source and destination from common English/Hinglish phrasing."""
    repo_pattern = r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)"
    patterns = [
        rf"(?:rename|renamed|renarte)\s+(?:the\s+)?(?:repo(?:sitory)?\s+)?"
        rf"{repo_pattern}(?:\s+repo(?:sitory)?)?\s+"
        rf"(?:to|as|into)\s+(.+)",
        rf"{repo_pattern}(?:\s+repo(?:sitory)?)?\s+"
        rf"(?:ka\s+naam|name)\s+(?:is|=|:|rakh(?:o)?|rakho)?\s*(.+)",
        rf"(?:repo(?:sitory)?\s+)?{repo_pattern}\s+"
        rf"(?:ka\s+naam|name)\s+(?:change|badal)(?:\s+karke|\s+to)?\s+(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
        if not match:
            continue
        repo_name = match.group(1).strip("`'\" ")
        new_name = match.group(2).strip("`'\" .,")
        new_name = re.sub(
            r"^(?:change|badal)(?:\s+karke|\s+to)?\s+",
            "",
            new_name,
            flags=re.IGNORECASE,
        )
        new_name = re.sub(
            r"\s+(?:karo|kar\s+do|rakho|please|na|ji)$",
            "",
            new_name,
            flags=re.IGNORECASE,
        )
        normalized_name = normalize_repo_name(new_name)
        if repo_name and normalized_name:
            return {"repo": repo_name, "new_name": normalized_name}
    return None


def extract_repo_name(text: str) -> Optional[str]:
    value = text.strip().strip("`'\" ")
    patterns = [
        r"(?:yes\s+)?(?:new|create|make|bana(?:o)?)\s+(?:a\s+)?repo(?:sitory)?\s+(?:named\s+|name\s+|naam\s+|is\s+)?[`'\"]?([A-Za-z0-9][A-Za-z0-9._-]{0,99})",
        r"(?:repo(?:sitory)?(?:\s+ka)?\s+(?:name|naam))\s*(?:is|=|:|rakho)?\s*[`'\"]?([A-Za-z0-9][A-Za-z0-9._-]{0,99})",
        r"(?:use|call it|name it|naam)\s*[`'\"]?([A-Za-z0-9][A-Za-z0-9._-]{0,99})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        return value
    return None


def _extract_balanced_json_object(text: str) -> Optional[str]:
    """Return the first top-level ``{...}`` object in text, using a
    depth-aware scan that ignores braces inside quoted strings and stops as
    soon as the object actually balances — so trailing prose the model adds
    after the JSON (or a stray extra ``}``) can't corrupt the slice the way
    a plain ``rfind("}")`` could.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _salvage_reply_and_action(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort recovery when the model's JSON is malformed (cut off
    mid-way, an odd stray character, etc). Pulls just the human-readable
    "reply" text out with a regex instead of ever showing the raw
    ``{"reply":...,"action":{...}}`` blob to the user in chat.
    """
    match = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if not match:
        return None
    try:
        reply_text = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        reply_text = match.group(1)
    action_obj = None
    action_match = re.search(r'"action"\s*:\s*(\{)', text)
    if action_match:
        candidate = _extract_balanced_json_object(text[action_match.start(1):])
        if candidate:
            try:
                parsed_action = json.loads(candidate)
                if isinstance(parsed_action, dict):
                    action_obj = parsed_action
            except json.JSONDecodeError:
                action_obj = None
    return {"reply": reply_text, "action": action_obj}


def parse_json_response(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    balanced = _extract_balanced_json_object(cleaned)
    if balanced is not None:
        try:
            parsed = json.loads(balanced)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    salvaged = _salvage_reply_and_action(cleaned)
    if salvaged is not None:
        return salvaged
    return {"reply": text.strip(), "action": None}


def api_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error", payload)
            if isinstance(error, dict):
                return str(error.get("message", error))
            return str(error)
    except ValueError:
        pass
    return response.text[:500] or f"HTTP {response.status_code}"


class ApiCallError(Exception):
    """Raised when an AI provider returns a non-2xx response.

    Carries the HTTP status code alongside the provider's error message so
    call_ai() can tell a rate limit apart from a retired/unknown model and
    react accordingly instead of just giving up.
    """

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def raise_for_response(response: requests.Response) -> None:
    if not response.ok:
        raise ApiCallError(response.status_code, api_error(response))


def call_openai_compatible(endpoint: str, key: str, model: str, messages: List[Dict[str, str]]) -> str:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    response = requests.post(
        endpoint,
        headers=headers,
        json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 1200},
        timeout=90,
    )
    raise_for_response(response)
    return str(response.json()["choices"][0]["message"]["content"])


def call_anthropic(key: str, model: str, messages: List[Dict[str, str]]) -> str:
    system = "\n\n".join(
        message["content"] for message in messages if message["role"] == "system"
    ) or SYSTEM_PROMPT
    api_messages = [message for message in messages if message["role"] != "system"]
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "system": system, "max_tokens": 1200, "messages": api_messages},
        timeout=90,
    )
    raise_for_response(response)
    blocks = response.json().get("content", [])
    return "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")


def call_gemini(key: str, model: str, messages: List[Dict[str, str]]) -> str:
    contents = []
    system_text = "\n\n".join(
        message["content"] for message in messages if message["role"] == "system"
    ) or SYSTEM_PROMPT
    for message in messages:
        if message["role"] == "system":
            continue
        contents.append(
            {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [{"text": message["content"]}],
            }
        )
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
        },
        timeout=90,
    )
    raise_for_response(response)
    return str(response.json()["candidates"][0]["content"]["parts"][0]["text"])


def models_endpoint_for(endpoint: str) -> str:
    base = re.sub(r"/chat/completions/?$", "", endpoint.strip())
    return base.rstrip("/") + "/models"


def fetch_models_openai_compatible(endpoint: str, key: str) -> List[str]:
    """List model ids from an OpenAI-compatible /v1/models endpoint."""
    try:
        response = requests.get(
            models_endpoint_for(endpoint),
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        if not response.ok:
            return []
        data = response.json().get("data", [])
        return [str(item.get("id")) for item in data if item.get("id")]
    except Exception:
        return []


def fetch_models_anthropic(key: str) -> List[str]:
    try:
        response = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=30,
        )
        if not response.ok:
            return []
        data = response.json().get("data", [])
        return [str(item.get("id")) for item in data if item.get("id")]
    except Exception:
        return []


def fetch_models_gemini(key: str) -> List[str]:
    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key},
            timeout=30,
        )
        if not response.ok:
            return []
        names = []
        for item in response.json().get("models", []):
            if "generateContent" in item.get("supportedGenerationMethods", []):
                name = str(item.get("name", "")).split("/")[-1]
                if name:
                    names.append(name)
        return names
    except Exception:
        return []


def fetch_available_models(provider: Dict[str, Any], key: str, endpoint: str) -> List[str]:
    if provider["kind"] == "anthropic":
        return fetch_models_anthropic(key)
    if provider["kind"] == "gemini":
        return fetch_models_gemini(key)
    if endpoint:
        return fetch_models_openai_compatible(endpoint, key)
    return []


def pick_fallback_model(models: List[str], exclude: set) -> Optional[str]:
    """Pick a reasonable chat model out of a provider's /models listing.

    Prefers small/fast/cheap models (flash, mini, haiku, etc.) so an
    automatic recovery never silently jumps someone onto their most
    expensive available model.
    """
    candidates = [
        model
        for model in models
        if model
        and model not in exclude
        and not any(bad in model.lower() for bad in NON_CHAT_MODEL_KEYWORDS)
    ]
    if not candidates:
        return None
    for keyword in PREFERRED_FALLBACK_KEYWORDS:
        matches = sorted((model for model in candidates if keyword in model.lower()), reverse=True)
        if matches:
            return matches[0]
    return sorted(candidates, reverse=True)[0]


def extract_suggested_model(message: str) -> Optional[str]:
    """Pull a replacement model name out of a provider's own error text.

    Providers increasingly tell you exactly what to switch to, e.g. "please
    update your code to use models/gemini-3.6-flash" or "migrate to
    openai/gpt-oss-20b" -- reuse that instead of guessing.
    """
    patterns = [
        r"use\s+models?/([A-Za-z0-9_.\-]+)",
        r"use\s+`?([A-Za-z0-9_./\-]+)`?\s+instead",
        r"replaced?\s+by\s+`?([A-Za-z0-9_./\-]+)`?",
        r"replacement model(?:\s+id)?[:\s]+`?([A-Za-z0-9_./\-]+)`?",
        r"migrat(?:e|ing) to\s+`?([A-Za-z0-9_./\-]+)`?",
        r"try\s+`?([A-Za-z0-9_./\-]+)`?\s+instead",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(").,`'\"")
            if candidate:
                return candidate
    return None


def extract_retry_delay_seconds(message: str, cap: float = 12.0) -> float:
    """Google's 429 errors usually include an exact suggested wait (e.g.
    'Please retry in 16.67s' or '108.049004ms'). Honor that instead of a
    blind fixed backoff, so a real retry has a better chance of succeeding —
    capped so the UI never blocks for an unreasonably long time."""
    match = re.search(r"retry in\s*([\d.]+)\s*(ms|s)\b", message, flags=re.IGNORECASE)
    if not match:
        return 0.0
    value = float(match.group(1))
    if match.group(2).lower() == "ms":
        value /= 1000.0
    return min(max(value, 0.0), cap)


def is_rate_limited(status_code: Optional[int], message: str) -> bool:
    if status_code == 429:
        return True
    lowered = message.lower()
    return "rate limit" in lowered or "too many requests" in lowered


def is_model_unavailable(status_code: Optional[int], message: str) -> bool:
    lowered = message.lower()
    keywords = (
        "no longer available",
        "not found",
        "does not exist",
        "deprecated",
        "decommissioned",
        "has been retired",
        "unknown model",
        "invalid model",
        "not supported",
    )
    return status_code in (400, 404) or any(keyword in lowered for keyword in keywords)


def dispatch_ai_call(
    provider: Dict[str, Any], key: str, model: str, endpoint: str, messages: List[Dict[str, str]]
) -> str:
    if provider["kind"] == "anthropic":
        return call_anthropic(key, model, messages)
    if provider["kind"] == "gemini":
        return call_gemini(key, model, messages)
    return call_openai_compatible(endpoint, key, model, messages)


MAX_AI_ATTEMPTS = 3


def build_session_context_note() -> str:
    """Summarize what the app already knows so the AI can resolve implicit
    references ("isko deploy kardo", "usi repo mein push karo") instead of
    asking the user to repeat information that is already available."""
    lines: List[str] = []
    gh_user = st.session_state.get("gh_user")
    lines.append(
        f"GitHub connected as: {gh_user}" if gh_user else "GitHub: not connected yet."
    )
    if st.session_state.get("current_repo"):
        lines.append(f"Repository currently open/in view: {st.session_state.current_repo}")
    if st.session_state.get("last_pushed_repo"):
        lines.append(f"Most recently pushed repository: {st.session_state.last_pushed_repo}")
    if st.session_state.get("project_files"):
        lines.append(
            f"A project ZIP is already uploaded ({len(st.session_state.project_files)} files)."
        )
        analysis = st.session_state.get("project_analysis")
        if isinstance(analysis, dict):
            lines.append(
                "Detected framework: {framework}; entrypoint: {entry}; "
                "recommended deploy platform: {platform}.".format(
                    framework=analysis.get("framework", "Unknown"),
                    entry=analysis.get("entrypoint") or "none found",
                    platform=analysis.get("recommended_platform", "unknown"),
                )
            )
    else:
        lines.append("No project ZIP uploaded yet.")
    if st.session_state.get("ec2_runner_label"):
        lines.append(f"AWS EC2 runner label configured: {st.session_state.ec2_runner_label}")
    live = st.session_state.get("live_view")
    if isinstance(live, dict) and live.get("kind") == "aws_ec2":
        live_data = live.get("data", {})
        if live_data.get("url"):
            lines.append(f"Last successful AWS EC2 live URL: {live_data.get('url')}")
    if isinstance(st.session_state.get("pending_deployment"), dict):
        lines.append(
            f"A deployment for {st.session_state.pending_deployment.get('repo')} "
            "is waiting only on a platform choice."
        )
    return "Current app state (use this to fill in missing details instead of " \
        "asking again when it's reasonably clear what the user means):\n- " + \
        "\n- ".join(lines)


def call_ai(user_text: str) -> Dict[str, Any]:
    key = st.session_state.api_key.strip()
    if not key:
        return {
            "reply": (
                "Select a provider and add an API key in the sidebar for free-language "
                "chat. The GitHub token needs to be connected separately."
            ),
            "action": None,
        }

    history = [
        {"role": message["role"], "content": message["content"]}
        for message in st.session_state.messages
    ]
    history.append({"role": "user", "content": user_text})
    provider = PROVIDERS[st.session_state.provider]
    model = selected_model()
    endpoint = (
        st.session_state.custom_endpoint.strip()
        if provider["kind"] == "custom"
        else provider.get("endpoint", "")
    )
    if provider["kind"] == "custom" and not endpoint:
        return {"reply": "Add the custom provider's endpoint.", "action": None}

    context_note = build_session_context_note()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": context_note},
    ] + history
    attempted_models = set()
    notice = ""

    for attempt in range(MAX_AI_ATTEMPTS):
        attempted_models.add(model)
        try:
            raw = dispatch_ai_call(provider, key, model, endpoint, messages)
            if model != st.session_state.model:
                st.session_state.model = model
            result = parse_json_response(raw)
            if notice and isinstance(result, dict):
                reply = str(result.get("reply") or "")
                result["reply"] = f"{notice}\n\n{reply}".strip()
            return result
        except requests.RequestException as exc:
            return {"reply": f"Could not connect to the AI service: {exc}", "action": None}
        except ApiCallError as exc:
            is_last_attempt = attempt == MAX_AI_ATTEMPTS - 1
            if not is_last_attempt and is_model_unavailable(exc.status_code, exc.message):
                suggested = extract_suggested_model(exc.message)
                if not suggested or suggested in attempted_models:
                    available = fetch_available_models(provider, key, endpoint)
                    suggested = pick_fallback_model(available, attempted_models)
                if suggested and suggested not in attempted_models:
                    notice = (
                        f"⚠️ Model `{model}` is not available ({exc.message}). "
                        f"Automatically trying `{suggested}`."
                    )
                    model = suggested
                    continue
            if not is_last_attempt and is_rate_limited(exc.status_code, exc.message):
                delay = extract_retry_delay_seconds(exc.message)
                time.sleep(delay if delay > 0 else 2 * (attempt + 1))
                continue
            return {"reply": f"AI request failed: {exc.message}", "action": None}
        except Exception as exc:
            return {"reply": f"AI request failed: {exc}", "action": None}

    return {
        "reply": "AI request failed: no model worked even after several attempts.",
        "action": None,
    }


PROJECT_GENERATION_SYSTEM_PROMPT = """
You are an expert {framework} developer. Write a complete, working {framework}
project based on the request below, the same way you would when asked to
write code directly for someone.

Rules:
- The project must actually run: no placeholder TODOs, no missing imports,
  no undefined functions or variables.
- Always include a working entrypoint file (for Streamlit, "app.py"), a
  "requirements.txt" listing every third-party package actually imported, and
  a short "README.md" explaining what the app does and how to run it.
- Keep the app reasonably self-contained and prefer well-known, commonly
  available packages.
- Reply in the same language as the request for any comments, but code
  identifiers should stay in English as usual.

Return ONLY valid JSON, with this exact shape and nothing else (no markdown
fences, no commentary outside the JSON):
{{
  "files": {{
    "app.py": "full file content as a single string, with real newline characters escaped as \\n",
    "requirements.txt": "...",
    "README.md": "..."
  }}
}}
Every key is a relative file path and every value is the full text content of
that file.
"""


def generate_project_code(description: str, framework: str) -> Dict[str, Any]:
    """Ask the connected AI provider to write a full new project as JSON files."""
    key = st.session_state.api_key.strip()
    if not key:
        return {
            "error": (
                "Select an AI provider and add an API key in the sidebar "
                "to generate a new project."
            )
        }
    provider = PROVIDERS[st.session_state.provider]
    model = selected_model()
    endpoint = (
        st.session_state.custom_endpoint.strip()
        if provider["kind"] == "custom"
        else provider.get("endpoint", "")
    )
    if provider["kind"] == "custom" and not endpoint:
        return {"error": "Add the custom provider's endpoint."}

    system = PROJECT_GENERATION_SYSTEM_PROMPT.format(framework=framework)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": description},
    ]
    attempted_models = set()

    for attempt in range(MAX_AI_ATTEMPTS):
        attempted_models.add(model)
        try:
            raw = dispatch_ai_call(provider, key, model, endpoint, messages)
            if model != st.session_state.model:
                st.session_state.model = model
            parsed = parse_json_response(raw)
            files = parsed.get("files") if isinstance(parsed, dict) else None
            if not isinstance(files, dict) or not files:
                return {
                    "error": (
                        "The AI did not generate valid project files. Make the "
                        "description a bit clearer and try again."
                    )
                }
            return {"files": files}
        except requests.RequestException as exc:
            return {"error": f"Could not connect to the AI service: {exc}"}
        except ApiCallError as exc:
            is_last_attempt = attempt == MAX_AI_ATTEMPTS - 1
            if not is_last_attempt and is_model_unavailable(exc.status_code, exc.message):
                suggested = extract_suggested_model(exc.message)
                if not suggested or suggested in attempted_models:
                    available = fetch_available_models(provider, key, endpoint)
                    suggested = pick_fallback_model(available, attempted_models)
                if suggested and suggested not in attempted_models:
                    model = suggested
                    continue
            if not is_last_attempt and is_rate_limited(exc.status_code, exc.message):
                delay = extract_retry_delay_seconds(exc.message)
                time.sleep(delay if delay > 0 else 2 * (attempt + 1))
                continue
            return {"error": f"AI request failed: {exc.message}"}
        except Exception as exc:
            return {"error": f"AI request failed: {exc}"}

    return {"error": "AI request failed: no model worked even after several attempts."}


def files_dict_to_project_files(files: Dict[str, Any]) -> List[Dict[str, Any]]:
    project_files: List[Dict[str, Any]] = []
    for raw_path, content in files.items():
        path = safe_zip_path(str(raw_path))
        if not path:
            continue
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2)
        project_files.append({"path": path, "content": str(content).encode("utf-8")})
    return project_files


def start_project_generation(description: str, framework: str = "streamlit", repo_name: str = "") -> str:
    """Generate a brand-new project with AI, then hand it to the existing push flow."""
    clean_description = description.strip() or f"A simple, useful {framework} app."
    with st.spinner(f"Generating {framework.capitalize()} project..."):
        result = generate_project_code(clean_description, framework)
    if "error" in result:
        return result["error"]

    project_files = files_dict_to_project_files(result["files"])
    if not project_files:
        return "The AI did not generate any valid file. Make the description a bit clearer and try again."

    st.session_state.project_files = project_files
    st.session_state.project_zip_name = f"Generated {framework} project"
    st.session_state.uploaded_signature = None
    st.session_state.project_analysis = analyze_project_files(project_files)
    st.session_state.last_push_report = None
    st.session_state.pending_push = False
    st.session_state.auto_deploy_after_push = True

    file_list = "\n".join(f"- 📄 `{item['path']}`" for item in project_files)
    summary = f"**{framework.capitalize()} project is ready:**\n\n{file_list}"

    if st.session_state.gh_user is None:
        return (
            f"{summary}\n\nTo push to GitHub now, first connect a GitHub "
            "token from the sidebar, then say `push`."
        )

    clean_repo_name = normalize_repo_name(repo_name) if repo_name else ""
    if clean_repo_name:
        action = {
            "type": "push_project",
            "repo_name": clean_repo_name,
            "private": st.session_state.private_repo,
            "commit_message": st.session_state.commit_message,
        }
        set_pending_confirmation(action)
        return f"{summary}\n\n{confirmation_preview(action)}"

    st.session_state.pending_push = True
    return f"{summary}\n\nTell me the repository name to push to GitHub."


def request_deployment(repo_name: str, platform: Optional[str] = None) -> str:
    if not st.session_state.project_analysis and st.session_state.gh_client is not None:
        try:
            st.session_state.project_analysis = gh_analyze_repo(repo_name)
        except Exception:
            # Deployment can still continue with the user's explicit platform
            # even when GitHub metadata cannot be read.
            pass
    chosen_platform = platform
    if not chosen_platform:
        selected = st.session_state.deployment_platform
        if selected != ASK_DEPLOYMENT_PLATFORM:
            chosen_platform = selected
    if not chosen_platform:
        st.session_state.pending_deployment = {"repo": repo_name, "suggested_platform": ""}
        return deployment_platform_prompt(st.session_state.project_analysis)

    action = deployment_action_for(chosen_platform, repo_name)
    st.session_state.pending_deployment = None
    set_pending_confirmation(action)
    return confirmation_preview(action)


def extract_deployment_repo_name(text: str) -> Optional[str]:
    """Extract a repo after a platform name, e.g. `deploy Render my-app`."""
    candidate = extract_open_repo_name(text)
    platform = extract_deployment_platform(text)
    if platform:
        without_platform = text
        aliases = [
            alias
            for alias, mapped_platform in DEPLOYMENT_PLATFORM_ALIASES.items()
            if mapped_platform == platform
        ]
        for alias in sorted(aliases, key=len, reverse=True):
            without_platform = re.sub(
                rf"\b{re.escape(alias)}\b",
                "",
                without_platform,
                count=1,
                flags=re.IGNORECASE,
            )
            if without_platform != text:
                break
        cleaned_candidate = extract_open_repo_name(without_platform)
        if cleaned_candidate:
            candidate = cleaned_candidate
    return candidate


def is_ai_quota_or_outage_error(reply_text: str) -> bool:
    lowered = reply_text.lower()
    return lowered.startswith("ai request failed") and any(
        marker in lowered
        for marker in (
            "quota", "rate limit", "429", "resource_exhausted",
            "too many requests", "could not connect",
        )
    )


def handle_message(
    user_text: str, attached_files: Optional[List[Dict[str, Any]]] = None
) -> str:
    # Files attached directly in the chat box (via the 📎 icon) mean the user
    # wants ONLY those exact files changed in the repo — never the full
    # st.session_state.project_files list. This is handled first, and uses
    # gh_update_files_subset (via the "update_files_subset" action) instead
    # of gh_push_project so the rest of the repo stays untouched.
    if attached_files:
        if st.session_state.gh_user is None:
            return "Connect a GitHub token from the sidebar first, then attach and send the file."
        repo_name = (
            extract_repo_name(user_text)
            or extract_open_repo_name(user_text)
            or st.session_state.current_repo
            or st.session_state.last_pushed_repo
        )
        if not repo_name:
            st.session_state.pending_file_push_files = attached_files
            return (
                "File received ✅. Which GitHub repository should I update just this "
                f"{'file' if len(attached_files) == 1 else 'files'} in? "
                "Tell me the repo name."
            )
        action = {
            "type": "update_files_subset",
            "repo_name": repo_name,
            "files": attached_files,
            "commit_message": st.session_state.commit_message,
        }
        set_pending_confirmation(action)
        emit_action_event("approval_requested", "waiting", "Approval required",
                          "Review the action and approve or deny it.", action)
        return confirmation_preview(action)

    if st.session_state.get("pending_file_push_files"):
        if user_text.strip().lower() in {"cancel", "cancel karo", "no", "nahi"}:
            st.session_state.pending_file_push_files = None
            return "OK, cancelled the file update."
        repo_name = extract_repo_name(user_text) or extract_open_repo_name(user_text)
        if not repo_name:
            return "Clearly tell me the repo name, e.g. `my-portfolio`."
        files = st.session_state.pending_file_push_files
        st.session_state.pending_file_push_files = None
        action = {
            "type": "update_files_subset",
            "repo_name": repo_name,
            "files": files,
            "commit_message": st.session_state.commit_message,
        }
        set_pending_confirmation(action)
        emit_action_event("approval_requested", "waiting", "Approval required",
                          "Review the action and approve or deny it.", action)
        return confirmation_preview(action)

    # Same pattern for the Render-connect wizard's final step: treat the next
    # message as a possible pasted Render API key instead of a normal chat
    # command.
    if (
        st.session_state.get("render_chat_wizard_active")
        and st.session_state.get("render_wizard_step") == 2
        and not (_render_api_key() and st.session_state.get("render_verified_once"))
    ):
        found_key = extract_render_api_key_from_text(user_text)
        if found_key:
            st.session_state.render_api_key = found_key
            status = render_connection_status()
            if status.get("ready"):
                st.session_state.render_verified_once = True
                st.session_state.render_chat_wizard_active = False
                return (
                    "✅ **Render connected!** Key received and verified. Now just say "
                    "**\"Render pe deploy karo\"** (with the repo) and I'll redeploy it "
                    "and give you the real live URL."
                )
            return (
                "Got the key but could not connect: "
                f"{status.get('message')}\n\nDouble-check and paste the Render API key again."
            )
        return (
            "I couldn't find a valid Render API key (it starts with `rnd_`) in this "
            "message. Copy it from the Render dashboard's API Keys page and paste it "
            "straight into this chat box."
        )

    pending_confirmation = st.session_state.pending_confirmation
    if isinstance(pending_confirmation, dict):
        if is_confirmation_yes(user_text):
            st.session_state.pending_confirmation = None
            emit_action_event("approval_received", "approved", "Approval received",
                              "User explicitly approved this action.", pending_confirmation)
            result = execute_action(pending_confirmation)
            emit_action_event("action_completed" if not str(result).lower().startswith(("error", "action failed", "repository", "file")) else "api_response",
                              "completed", "Action finished", "Execution result recorded.", pending_confirmation)
            st.session_state.action_history.append({
                "action_id": st.session_state.active_action_id,
                "type": pending_confirmation.get("type"),
                "status": "completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            should_auto_deploy = (
                pending_confirmation.get("type") == "push_project"
                and st.session_state.auto_deploy_after_push
            )
            st.session_state.auto_deploy_after_push = False
            if should_auto_deploy:
                push_report = st.session_state.last_push_report or {}
                pushed_ok = bool(push_report.get("created") or push_report.get("updated"))
                if pushed_ok and st.session_state.last_pushed_repo:
                    try:
                        deploy_info = request_deployment(
                            st.session_state.last_pushed_repo
                        )
                        result += "\n\n---\n\n" + deploy_info
                    except Exception as exc:
                        result += (
                            "\n\nCould not automatically prepare a deployment option: "
                            f"{github_error(exc)}"
                        )
            return result
        if is_confirmation_no(user_text):
            st.session_state.pending_confirmation = None
            st.session_state.auto_deploy_after_push = False
            return "OK, cancelled the action."
        return "Please type `yes`/`confirm` or `no`/`cancel`."

    pending_deployment = st.session_state.pending_deployment
    if isinstance(pending_deployment, dict):
        if is_confirmation_no(user_text):
            st.session_state.pending_deployment = None
            return "OK, cancelled the deployment request."
        platform = extract_deployment_platform(user_text)
        if not platform and is_confirmation_yes(user_text):
            suggested = pending_deployment.get("suggested_platform")
            if suggested in DEPLOYMENT_PLATFORM_OPTIONS:
                platform = suggested
        if not platform:
            return deployment_platform_prompt(st.session_state.project_analysis)
        return request_deployment(str(pending_deployment.get("repo", "")), platform)

    if st.session_state.pending_push:
        if user_text.strip().lower() in {"cancel", "cancel karo", "no", "nahi"}:
            st.session_state.pending_push = False
            return "OK, cancelled the GitHub push."
        repo_name = extract_repo_name(user_text)
        if not repo_name:
            return "Clearly tell me the repo name, e.g. `my-portfolio`."
        st.session_state.pending_push = False
        action = {
            "type": "push_project",
            "repo_name": repo_name,
            "private": st.session_state.private_repo,
            "commit_message": st.session_state.commit_message,
        }
        set_pending_confirmation(action)
        emit_action_event("approval_requested", "waiting", "Approval required", "Review the action and approve or deny it.", action)
        return confirmation_preview(action)

    # Never let the AI improvise a request for AWS credentials in chat - that
    # must always go through the sidebar's guarded step-by-step wizard, so
    # this check runs before the AI call and short-circuits it.
    if looks_like_aws_connect_request(user_text):
        return aws_connect_guidance_reply()

    # Same guard for Render: never let the AI improvise a request for a
    # Render API key in chat - that must go through this guarded wizard.
    if looks_like_render_connect_request(user_text):
        return render_connect_guidance_reply()

    # Prefer real language understanding over keyword-matching whenever an AI
    # key is configured. The rule-based `looks_like_*` checks below exist as a
    # fallback so the app still does something useful without an API key, if
    # the AI call itself fails, or if the AI provider's quota/rate limit is
    # exhausted (free tiers are easy to hit once every message calls the AI).
    quota_notice = ""
    if st.session_state.api_key.strip():
        ai_result = call_ai(user_text)
        action = ai_result.get("action")
        if isinstance(action, dict) and action.get("type"):
            if action.get("type") == "deploy_aws":
                action["type"] = "deploy_aws_ec2"
                action.setdefault("port", "")
                action.setdefault("instance_id", st.session_state.get("aws_ec2_instance_id", ""))
                action.pop("workflow", None)
                action.pop("inputs", None)
            if action.get("type") == "generate_project":
                return start_project_generation(
                    str(action.get("description") or user_text),
                    str(action.get("framework") or "streamlit"),
                    str(action.get("repo_name") or ""),
                )
            if needs_confirmation(action):
                set_pending_confirmation(action)
                emit_action_event("approval_requested", "waiting", "Approval required", "Review the action and approve or deny it.", action)
                return confirmation_preview(action)
            return execute_action(action)
        reply = str(ai_result.get("reply") or "").strip()
        if reply.startswith("{") and '"reply"' in reply:
            # Defensive net: if a provider ever double-encodes and the raw
            # JSON blob ends up sitting inside the "reply" field itself,
            # never show that literal text in chat — pull the human-readable
            # part back out instead.
            salvage = _salvage_reply_and_action(reply)
            if salvage and str(salvage.get("reply") or "").strip():
                reply = str(salvage["reply"]).strip()
        if reply and is_ai_quota_or_outage_error(reply):
            # Don't show the raw quota/outage error for every single message —
            # quietly fall back to rule-based matching below so basic commands
            # (push, deploy, list repos, settings, ...) keep working. Only
            # surface the quota notice if the rule-based fallback also can't
            # figure out what was meant.
            quota_notice = (
                "⚠️ The AI (free tier) quota is currently exhausted, try again "
                "in a bit — until then, basic commands (push, deploy, "
                "list repos, settings, analyze) will keep working via "
                "keyword-based matching.\n\n"
            )
        elif reply:
            return reply
        # Fall through to the rule-based matching below only if the AI
        # returned nothing usable, or its quota/outage error was suppressed.

    return quota_notice + rule_based_reply(user_text)


def rule_based_reply(user_text: str) -> str:
    if looks_like_generate_project(user_text):
        framework = extract_generate_project_framework(user_text)
        repo_hint = extract_repo_name(user_text) or ""
        return start_project_generation(user_text, framework=framework, repo_name=repo_hint)

    if looks_like_analyze(user_text):
        return project_analysis_text(st.session_state.project_analysis)

    if looks_like_create_repo(user_text):
        if st.session_state.gh_user is None:
            return "Connect your GitHub token from the sidebar first."
        repo_name = extract_repo_name(user_text)
        if not repo_name:
            return "Tell me the new repository's name, e.g. `create repo my-agent`."
        action = {
            "type": "create_repo",
            "name": repo_name,
            "owner": "",
            "private": st.session_state.private_repo,
            "description": "",
        }
        set_pending_confirmation(action)
        emit_action_event("approval_requested", "waiting", "Approval required", "Review the action and approve or deny it.", action)
        return confirmation_preview(action)

    if looks_like_rename_repo(user_text):
        if st.session_state.gh_user is None:
            return "Connect your GitHub token from the sidebar first."
        rename_details = extract_rename_details(user_text)
        if not rename_details:
            return (
                "Give me both the source and the new name to rename, e.g. "
                "`rename cancer repo to cancer-project`."
            )
        action = {"type": "rename_repo", **rename_details}
        set_pending_confirmation(action)
        emit_action_event("approval_requested", "waiting", "Approval required", "Review the action and approve or deny it.", action)
        return confirmation_preview(action)

    if looks_like_delete_repo(user_text):
        if st.session_state.gh_user is None:
            return "Connect your GitHub token from the sidebar first."
        repo_name = extract_open_repo_name(user_text)
        if not repo_name:
            return "Which repository should be deleted? Tell me the name, e.g. `delete repo old-agent`."
        action = {"type": "delete_repo", "repo": repo_name}
        set_pending_confirmation(action)
        emit_action_event("approval_requested", "waiting", "Approval required", "Review the action and approve or deny it.", action)
        return confirmation_preview(action)

    if looks_like_list_repos(user_text):
        if st.session_state.gh_user is None:
            return "Connect your GitHub token from the sidebar first."
        try:
            return gh_list_repos()
        except Exception as exc:
            return f"Could not list repositories: {github_error(exc)}"

    if looks_like_deploy(user_text):
        repo_name = (
            extract_deployment_repo_name(user_text)
            or st.session_state.last_pushed_repo
            or st.session_state.current_repo
        )
        if not repo_name:
            return (
                "Push the project to a GitHub repository before deployment, "
                "or give a repository name in the message like `deploy Render owner/repo`."
            )
        return request_deployment(
            repo_name,
            extract_deployment_platform(user_text),
        )

    if looks_like_open_repo(user_text):
        if st.session_state.gh_user is None:
            return "Connect your GitHub token from the sidebar first."
        repo_name = extract_open_repo_name(user_text)
        if not repo_name:
            return "Which repository should be opened? Tell me the name, e.g. `cancer`."
        try:
            return gh_open_repo(repo_name)
        except Exception as exc:
            return f"Could not open the repository: {github_error(exc)}"

    if looks_like_settings(user_text):
        if st.session_state.gh_user is None:
            return "Connect your GitHub token from the sidebar first."
        repo_name = (
            extract_settings_repo_name(user_text)
            or st.session_state.current_repo
            or st.session_state.last_pushed_repo
        )
        if not repo_name:
            return "Which repository's settings should be opened? Tell me the name, e.g. `open cancer settings`."
        changes = extract_settings_changes(user_text)
        if changes:
            action = {"type": "update_repo_settings", "repo": repo_name, "changes": changes}
            set_pending_confirmation(action)
            emit_action_event("approval_requested", "waiting", "Approval required", "Review the action and approve or deny it.", action)
            return confirmation_preview(action)
        try:
            return gh_repo_settings(repo_name)
        except Exception as exc:
            return f"Could not load the settings: {github_error(exc)}"

    if looks_like_push(user_text):
        if not st.session_state.project_files:
            return "Upload your project ZIP in the sidebar's **Project ZIP Upload** section first."
        if st.session_state.gh_user is None:
            return "Connect your GitHub token from the sidebar first."
        st.session_state.pending_push = True
        return "Sure. Before I push — what should I name the GitHub repository?"

    return "I didn't understand that. Try phrasing it differently, or add an AI API key in the sidebar so I can also understand free-language commands."


GITHUB_VIEW_CSS = """
<style>
.ghv-box {background:#0d1117;border:1px solid #30363d;border-radius:14px;
  padding:0;overflow:hidden;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
  color:#c9d1d9;margin-bottom:10px;box-shadow:0 14px 34px -22px rgba(0,0,0,.8);}
.ghv-header {background:#161b22;border-bottom:1px solid #30363d;padding:13px 16px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;}
.ghv-title {font-size:16px;font-weight:700;color:#8fb8ff;}
.ghv-badge {font-size:11px;border:1px solid #30363d;border-radius:2em;padding:2px 9px;
  color:#8b949e;margin-left:6px;background:rgba(255,255,255,.03);}
.ghv-tabs {display:flex;gap:2px;background:#161b22;border-bottom:1px solid #30363d;
  padding:0 12px;flex-wrap:wrap;}
.ghv-tab {padding:9px 12px;font-size:13px;color:#8b949e;border-bottom:2px solid transparent;}
.ghv-tab.active {color:#c9d1d9;border-bottom:2px solid #7bd8c9;font-weight:600;}
.ghv-body {padding:15px 16px;}
.ghv-row {display:flex;justify-content:space-between;align-items:center;
  padding:8px 0;border-bottom:1px solid #21262d;font-size:13px;}
.ghv-row:last-child {border-bottom:none;}
.ghv-name {color:#58a6ff;}
.ghv-meta {color:#8b949e;font-size:12px;}
.ghv-empty {color:#8b949e;font-size:13px;padding:24px;text-align:center;
  border:1px dashed #30363d;border-radius:8px;background:#0d1117;}
.ghv-readme {background:#0d1117;border:1px solid #21262d;border-radius:6px;
  padding:12px;margin-top:10px;font-size:12px;white-space:pre-wrap;
  max-height:220px;overflow-y:auto;color:#c9d1d9;}
.ghv-setting-row {display:flex;justify-content:space-between;align-items:center;
  padding:10px 0;border-bottom:1px solid #21262d;}
.ghv-toggle-on {color:#3fb950;font-weight:600;font-size:12px;}
.ghv-toggle-off {color:#8b949e;font-weight:600;font-size:12px;}
.ghv-code {background:#0d1117;border:1px solid #21262d;border-radius:6px;
  padding:12px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;
  font-size:12px;white-space:pre-wrap;max-height:260px;overflow-y:auto;}
.ghv-issue-open {color:#3fb950;}
.ghv-issue-closed {color:#a371f7;}
</style>
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _ghv_header(full_name: str, right_badges: List[str], active_tab: str) -> str:
    badges_html = "".join(f'<span class="ghv-badge">{_esc(badge)}</span>' for badge in right_badges)
    tabs = ["Code", "Issues", "Pull requests", "Actions", "Releases", "Settings"]
    tabs_html = "".join(
        f'<span class="ghv-tab{" active" if tab == active_tab else ""}">{tab}</span>'
        for tab in tabs
    )
    return (
        '<div class="ghv-box">'
        f'<div class="ghv-header"><span class="ghv-title">📦 {_esc(full_name)}</span>'
        f'<span>{badges_html}</span></div>'
        f'<div class="ghv-tabs">{tabs_html}</div>'
        '<div class="ghv-body">'
    )


_GHV_FOOTER = "</div></div>"


def render_repo_view(data: Dict[str, Any]) -> None:
    badges = ["🔒 Private" if data.get("private") else "🌐 Public"]
    badges.append(f"⭐ {data.get('stars', 0)}")
    badges.append(f"🍴 {data.get('forks', 0)}")
    parts = [_ghv_header(data["full_name"], badges, "Code")]
    if data.get("description"):
        parts.append(f'<div class="ghv-meta" style="margin-bottom:10px;">{_esc(data["description"])}</div>')
    parts.append(f'<div class="ghv-meta">Branch: <b>{_esc(data.get("default_branch", "main"))}</b></div>')
    parts.append('<div style="margin-top:10px;">')
    for entry in data.get("entries", [])[:40]:
        icon = "📁" if entry["type"] == "dir" else "📄"
        parts.append(
            f'<div class="ghv-row"><span>{icon} <span class="ghv-name">{_esc(entry["name"])}</span></span>'
            f'<span class="ghv-meta">{_esc(entry["type"])}</span></div>'
        )
    parts.append("</div>")
    if data.get("readme"):
        parts.append('<div class="ghv-meta" style="margin-top:12px;">📘 README.md preview</div>')
        parts.append(f'<div class="ghv-readme">{_esc(data["readme"])}</div>')
    parts.append(_GHV_FOOTER)
    st.markdown("".join(parts), unsafe_allow_html=True)
    st.link_button("↗ Open on github.com", data.get("html_url", "https://github.com"), use_container_width=True)


def render_settings_view(data: Dict[str, Any]) -> None:
    badges = ["🔒 Private" if data.get("private") else "🌐 Public"]
    parts = [_ghv_header(data["full_name"], badges, "Settings")]
    parts.append('<div class="ghv-meta" style="margin-bottom:6px;">General</div>')

    def toggle_row(label: str, value: bool) -> str:
        cls = "ghv-toggle-on" if value else "ghv-toggle-off"
        state = "● Enabled" if value else "○ Disabled"
        return f'<div class="ghv-setting-row"><span>{_esc(label)}</span><span class="{cls}">{state}</span></div>'

    parts.append(
        f'<div class="ghv-setting-row"><span>Repository name</span>'
        f'<span class="ghv-meta">{_esc(data["full_name"].split("/")[-1])}</span></div>'
    )
    parts.append(
        f'<div class="ghv-setting-row"><span>Description</span>'
        f'<span class="ghv-meta">{_esc(data.get("description") or "(none)")}</span></div>'
    )
    parts.append(
        f'<div class="ghv-setting-row"><span>Default branch</span>'
        f'<span class="ghv-meta">{_esc(data.get("default_branch", "main"))}</span></div>'
    )
    parts.append(
        f'<div class="ghv-setting-row"><span>Visibility</span>'
        f'<span class="ghv-meta">{"Private" if data.get("private") else "Public"}</span></div>'
    )
    parts.append('<div class="ghv-meta" style="margin:14px 0 6px;">Features</div>')
    parts.append(toggle_row("Issues", data.get("has_issues", False)))
    parts.append(toggle_row("Wiki", data.get("has_wiki", False)))
    parts.append(toggle_row("Projects", data.get("has_projects", False)))
    parts.append('<div class="ghv-meta" style="margin:14px 0 6px;">Pull Requests</div>')
    parts.append(toggle_row("Automatically delete head branches", data.get("delete_branch_on_merge", False)))
    if data.get("topics"):
        parts.append('<div class="ghv-meta" style="margin:14px 0 6px;">Topics</div>')
        parts.append(
            "".join(f'<span class="ghv-badge">{_esc(t)}</span>' for t in data["topics"])
        )
    parts.append(_GHV_FOOTER)
    st.markdown("".join(parts), unsafe_allow_html=True)
    st.link_button("↗ Open Settings on github.com", data.get("settings_url", "https://github.com"), use_container_width=True)


def render_issues_view(data: Dict[str, Any]) -> None:
    parts = [_ghv_header(data["full_name"], [f"{len(data.get('issues', []))} open"], "Issues")]
    if not data.get("issues"):
        parts.append('<div class="ghv-meta">No open issues.</div>')
    for issue in data.get("issues", []):
        cls = "ghv-issue-closed" if issue["state"] == "closed" else "ghv-issue-open"
        icon = "🟣" if issue.get("is_pull_request") else "🟢"
        parts.append(
            f'<div class="ghv-row"><span>{icon} <span class="ghv-name">{_esc(issue["title"])}</span></span>'
            f'<span class="{cls}">#{issue["number"]} · {_esc(issue["state"])}</span></div>'
        )
    parts.append(_GHV_FOOTER)
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_releases_view(data: Dict[str, Any]) -> None:
    parts = [_ghv_header(data["full_name"], [f"{len(data.get('releases', []))} releases"], "Releases")]
    if not data.get("releases"):
        parts.append('<div class="ghv-meta">No releases yet.</div>')
    for release in data.get("releases", []):
        tag = "🏷️ " + _esc(release["tag_name"])
        badge = "Draft" if release.get("draft") else ("Pre-release" if release.get("prerelease") else "Latest")
        parts.append(
            f'<div class="ghv-row"><span class="ghv-name">{tag} {_esc(release.get("title", ""))}</span>'
            f'<span class="ghv-meta">{badge}</span></div>'
        )
    parts.append(_GHV_FOOTER)
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_workflows_view(data: Dict[str, Any]) -> None:
    parts = [_ghv_header(data["full_name"], [f"{len(data.get('workflows', []))} workflows"], "Actions")]
    if not data.get("workflows"):
        parts.append('<div class="ghv-meta">No GitHub Actions workflow found.</div>')
    for workflow in data.get("workflows", []):
        parts.append(
            f'<div class="ghv-row"><span class="ghv-name">⚙️ {_esc(workflow["name"])}</span>'
            f'<span class="ghv-meta">{_esc(workflow["state"])}</span></div>'
        )
    parts.append(_GHV_FOOTER)
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_file_view(data: Dict[str, Any]) -> None:
    parts = [_ghv_header(data["full_name"], [], "Code")]
    parts.append(f'<div class="ghv-meta" style="margin-bottom:8px;">📄 {_esc(data.get("path", ""))}</div>')
    content = data.get("content", "")
    if data.get("truncated"):
        content += "\n...(truncated)"
    parts.append(f'<div class="ghv-code">{_esc(content)}</div>')
    parts.append(_GHV_FOOTER)
    st.markdown("".join(parts), unsafe_allow_html=True)
    st.link_button("↗ Open file on github.com", data.get("html_url", "https://github.com"), use_container_width=True)


def render_repos_list_view(data: Dict[str, Any]) -> None:
    parts = [
        '<div class="ghv-box"><div class="ghv-header">'
        f'<span class="ghv-title">👤 {_esc(data.get("owner", ""))} — Repositories</span></div>'
        '<div class="ghv-body">'
    ]
    if not data.get("repos"):
        parts.append('<div class="ghv-meta">No repositories found.</div>')
    for repo in data.get("repos", []):
        badge = "🔒 Private" if repo.get("private") else "🌐 Public"
        parts.append(
            f'<div class="ghv-row"><span class="ghv-name">📦 {_esc(repo["full_name"])}</span>'
            f'<span class="ghv-meta">{badge} · ⭐ {repo.get("stars", 0)}</span></div>'
        )
    parts.append(_GHV_FOOTER)
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_aws_ec2_view(data: Dict[str, Any]) -> None:
    url = data.get("url", "")
    parts = [
        '<div class="ghv-box"><div class="ghv-header"><span class="ghv-title">🚀 AWS EC2 Deployment</span><span><span class="ghv-badge">Docker</span><span class="ghv-badge">Live</span></span></div><div class="ghv-body">',
        f'<div class="ghv-row"><span>Repository</span><span class="ghv-name">{_esc(data.get("repository",""))}</span></div>',
        f'<div class="ghv-row"><span>EC2 Instance</span><span class="ghv-meta">{_esc(data.get("instance_id","unknown"))}</span></div>',
        f'<div class="ghv-row"><span>Host</span><span class="ghv-meta">{_esc(data.get("host","unknown"))}</span></div>',
        f'<div class="ghv-row"><span>Port</span><span class="ghv-meta">{_esc(data.get("port","unknown"))}</span></div>',
        f'<div class="ghv-row"><span>Container</span><span class="ghv-meta">{_esc(data.get("container","unknown"))}</span></div>',
        f'<div class="ghv-row"><span>Deployment Mode</span><span class="ghv-meta">{_esc(data.get("deployment_mode","AWS SSM direct"))}</span></div>',
        f'<div class="ghv-row"><span>Security Group</span><span class="ghv-meta">{_esc(data.get("security_group","unknown"))}</span></div>',
        f'<div style="margin-top:14px;padding:14px;border:1px solid #30363d;border-radius:8px;background:#161b22;"><div class="ghv-meta">LIVE APPLICATION</div><div style="font-size:18px;font-weight:700;margin-top:4px;">{_esc(url)}</div></div>',
        _GHV_FOOTER]
    st.markdown("".join(parts), unsafe_allow_html=True)
    if url:
        st.link_button("🌐 Open Live App", url, use_container_width=True)
    if data.get("workflow_url"):
        st.link_button("⚙️ Open GitHub Actions Run", data["workflow_url"], use_container_width=True)

def render_live_github_view() -> None:
    st.markdown(GITHUB_VIEW_CSS, unsafe_allow_html=True)
    live = st.session_state.live_view
    if not live:
        st.markdown(
            '<div class="ghv-empty">Whenever the agent performs a GitHub action — opening a repo, '
            'settings, issues, releases, workflows — a screen that looks just like GitHub '
            'will update live right here.</div>',
            unsafe_allow_html=True,
        )
        return
    kind = live.get("kind")
    data = live.get("data", {})
    renderers = {
        "repo": render_repo_view,
        "settings": render_settings_view,
        "issues": render_issues_view,
        "releases": render_releases_view,
        "workflows": render_workflows_view,
        "file": render_file_view,
        "repos_list": render_repos_list_view,
        "aws_ec2": render_aws_ec2_view,
    }
    renderer = renderers.get(kind)
    if renderer:
        renderer(data)
    else:
        st.json(data)


GITHUB_OAUTH_SCOPE = "repo delete_repo workflow"

# --- App-wide ("Sign in with GitHub") OAuth support -------------------------
# If the *developer* (you) creates ONE GitHub OAuth App and sets these three
# environment variables on the server, every visitor just clicks "Authorize
# with GitHub" - no one else ever needs to see, create, or paste a Client
# ID/Secret. Each visitor still gets their OWN GitHub access token from their
# own consent screen; the agent only ever acts within that user's token
# permissions. This is the standard multi-user OAuth pattern (same as "Sign
# in with Google/GitHub" buttons on other sites).
#
# If these server variables are missing, the public app intentionally does
# not expose manual Client ID/Secret fields to visitors.
_ENV_GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "").strip()
_ENV_GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
_ENV_PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
GITHUB_OAUTH_APP_WIDE = bool(_ENV_GITHUB_CLIENT_ID and _ENV_GITHUB_CLIENT_SECRET)

# OAuth redirects can establish a fresh Streamlit browser session, AND the
# Streamlit process itself can restart between "Authorize with GitHub" and
# GitHub's redirect back (file-watcher auto-reload, Replit rebuild, etc).
# A plain in-memory dict - even one wrapped in st.cache_resource - does not
# survive a process restart, which is exactly what produces "OAuth session
# expire ho gaya ya state match nahi hua" even right after a fresh click.
# So the short-lived OAuth transaction is persisted to a small file on disk
# instead. The Client Secret never enters the URL, and each entry is
# single-use with a short TTL.
_OAUTH_PENDING_TTL_SECONDS = 10 * 60
_OAUTH_PENDING_PATH = Path(tempfile.gettempdir()) / "github_agent_oauth_pending.json"


def _load_oauth_pending() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_OAUTH_PENDING_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_oauth_pending(store: Dict[str, Dict[str, Any]]) -> None:
    try:
        _OAUTH_PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _OAUTH_PENDING_PATH.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(store, fh)
        os.replace(tmp_path, _OAUTH_PENDING_PATH)
    except OSError:
        pass  # best-effort persistence; falls back to same-session check


def _cleanup_oauth_pending(store: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    now = time.time()
    return {
        state: item
        for state, item in store.items()
        if now - float(item.get("created_at", 0)) <= _OAUTH_PENDING_TTL_SECONDS
    }


def _remember_oauth_request(state: str, client_id: str, client_secret: str, redirect_uri: str) -> None:
    store = _cleanup_oauth_pending(_load_oauth_pending())
    store[state] = {
        "created_at": time.time(),
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    _save_oauth_pending(store)


def _take_oauth_request(state: str) -> Optional[Dict[str, Any]]:
    store = _cleanup_oauth_pending(_load_oauth_pending())
    item = store.pop(state, None)
    _save_oauth_pending(store)
    if not item:
        return None
    if time.time() - float(item.get("created_at", 0)) > _OAUTH_PENDING_TTL_SECONDS:
        return None
    return item


def github_oauth_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    return (
        "https://github.com/login/oauth/authorize"
        f"?client_id={quote(client_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&scope={quote(GITHUB_OAUTH_SCOPE)}"
        f"&state={quote(state)}"
    )


def exchange_github_oauth_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> str:
    """Trade the callback ?code=... for a real access token, exactly the way
    github.com does it after the person clicks 'Authorize'."""
    response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(f"Got an unexpected response from GitHub (HTTP {response.status_code}).")
    if "error" in payload:
        raise RuntimeError(payload.get("error_description") or payload.get("error"))
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("GitHub did not return an access token. Check the OAuth App's settings.")
    return str(token)


def connect_github_with_token(token: str) -> None:
    client = Github(token.strip())
    user = client.get_user()
    _ = user.login  # force a call so a bad token fails immediately
    st.session_state.gh_client = client
    st.session_state.gh_user = user
    st.session_state.github_access_token = token.strip()


def handle_github_oauth_callback() -> None:
    """Run once per rerun, before the sidebar draws, so an OAuth redirect
    back into the app connects automatically - same effect as the
    'Authorize' screen the person just approved on github.com."""
    params = st.query_params
    code = params.get("code")
    returned_state = params.get("state")
    oauth_error = params.get("error")
    if not code and not oauth_error:
        return

    # GitHub may redirect to a fresh Streamlit session. Therefore do not rely
    # on session_state for the OAuth handshake credentials. Look them up by
    # the one-time random state created before leaving this app.
    request_data = _take_oauth_request(str(returned_state)) if returned_state else None

    # Same-session fallback is useful when the Streamlit websocket survives.
    if request_data is None and returned_state == st.session_state.github_oauth_state:
        client_id = st.session_state.github_oauth_client_id.strip()
        client_secret = st.session_state.github_oauth_client_secret.strip()
        redirect_uri = st.session_state.github_oauth_redirect_uri.strip()
        if client_id and client_secret and redirect_uri:
            request_data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            }

    if not returned_state or request_data is None:
        st.session_state.github_oauth_error = (
            "The GitHub authorization session expired or the state did not match. "
            "Please click Connect GitHub again."
        )
        st.query_params.clear()
        return

    if oauth_error:
        description = params.get("error_description") or oauth_error
        st.session_state.github_oauth_error = f"GitHub authorization was cancelled/failed: {description}"
        st.query_params.clear()
        return

    client_id = str(request_data["client_id"]).strip()
    client_secret = str(request_data["client_secret"]).strip()
    redirect_uri = str(request_data["redirect_uri"]).strip()
    st.session_state.github_oauth_client_id = client_id
    st.session_state.github_oauth_client_secret = client_secret
    st.session_state.github_oauth_redirect_uri = redirect_uri

    try:
        token = exchange_github_oauth_code(client_id, client_secret, code, redirect_uri)
        connect_github_with_token(token)
        st.session_state.github_oauth_error = None
    except Exception as exc:
        st.session_state.gh_client = None
        st.session_state.gh_user = None
        st.session_state.github_oauth_error = f"GitHub authorized, but could not connect: {github_error(exc)}"
    st.query_params.clear()


init_state()
handle_github_oauth_callback()

with st.sidebar:
    st.header("🔑 GitHub Connection")

    if st.session_state.github_oauth_error:
        st.error(st.session_state.github_oauth_error)

    # Public production mode: users only see one Connect GitHub button.
    # The OAuth Client ID/Secret live only in server environment variables.
    if GITHUB_OAUTH_APP_WIDE:
        st.caption("Connect your own GitHub account. Each user's authorization is separate.")
        redirect_uri = _ENV_PUBLIC_APP_URL
        if redirect_uri:
            oauth_state = st.session_state.github_oauth_state
            _remember_oauth_request(
                oauth_state, _ENV_GITHUB_CLIENT_ID, _ENV_GITHUB_CLIENT_SECRET, redirect_uri
            )
            authorize_url = github_oauth_authorize_url(
                _ENV_GITHUB_CLIENT_ID, redirect_uri, oauth_state
            )
            st.link_button("🔗 Connect GitHub", authorize_url, use_container_width=True, type="primary")
        else:
            st.error("Server misconfiguration: PUBLIC_APP_URL is not set.")
    else:
        st.warning(
            "GitHub OAuth is not configured on this server. "
            "Set GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and PUBLIC_APP_URL as server secrets."
        )
        st.caption("Users should never enter your OAuth Client Secret or a personal GitHub token.")

    if st.session_state.gh_user and st.button(
        "Disconnect GitHub", key="oauth_disconnect", use_container_width=True
    ):
        st.session_state.gh_client = None
        st.session_state.gh_user = None
        st.session_state.github_access_token = ""
        st.info("GitHub disconnected.")

    if st.session_state.gh_user:
        st.success(f"Connected: {st.session_state.gh_user.login}")
        st.caption(
            "Connected means the token is valid; actions like repo create/delete "
            "or workflow runs may still need separate permissions."
        )
    else:
        st.warning("GitHub is not connected")

    with st.expander("Create/delete permission fix"):
        st.markdown(
            "If you get `Resource not accessible by personal access token`, "
            "the GitHub token is missing permission for that action.\n\n"
            "- Classic PAT: `repo` for private/repository actions; `delete_repo` "
            "to delete a repository.\n"
            "- Fine-grained PAT: set the correct account/organization as the "
            "resource owner and give Administration, Contents, Issues, Pull "
            "requests Read and write access. Actions also needs Read and write "
            "access to run workflows.\n"
            "- Organization token policy may block repo creation/deletion.\n\n"
            "[Open GitHub token settings](https://github.com/settings/personal-access-tokens/new)"
        )

    st.divider()
    st.header("🧠 AI API Key")
    st.caption("Choose a provider. The key stays only in this browser session.")
    st.selectbox(
        "AI provider",
        list(PROVIDERS.keys()),
        key="provider",
        on_change=sync_provider_model,
    )
    provider_info = PROVIDERS[st.session_state.provider]
    st.text_input(
        "Model name (not API key)",
        key="model",
        help=f"Provider default: {provider_info['model']}. This updates automatically when you change the provider.",
    )
    if provider_info["kind"] == "custom":
        st.text_input(
            "Custom OpenAI-compatible endpoint",
            key="custom_endpoint",
            placeholder="https://your-host/v1/chat/completions",
        )
    st.text_input(
        f"{st.session_state.provider} API key",
        type="password",
        key="api_key",
        help=provider_info["help"],
    )
    if st.session_state.api_key:
        st.success("AI API key is set")
    else:
        st.info("Free-language chat will work once you add an API key")

    with st.expander("🎤 Mic / Voice settings"):
        st.caption(
            "Mic transcription (speech-to-text) only works via **OpenAI**'s or "
            "**Groq**'s Whisper model. If your AI provider above isn't "
            "OpenAI/Groq (e.g. Anthropic, Gemini, Mistral), add a small separate "
            "OpenAI or Groq key here — it'll be used only for the mic, "
            "not for chat."
        )
        if st.session_state.provider in {"OpenAI", "Groq"}:
            st.info(
                f"Your AI provider is already **{st.session_state.provider}**, "
                "so the mic will work with the key above — no need to fill "
                "anything in below."
            )
        st.selectbox(
            "Voice (mic) provider",
            ["", "OpenAI", "Groq"],
            key="voice_provider",
            format_func=lambda v: "Use main AI provider above" if v == "" else v,
        )
        if st.session_state.voice_provider:
            st.text_input(
                f"{st.session_state.voice_provider} API key (voice only)",
                type="password",
                key="voice_api_key",
            )

    if st.button("🔄 Auto-detect working model", use_container_width=True):
        if not st.session_state.api_key.strip():
            st.error("Enter an API key first.")
        else:
            detect_endpoint = (
                st.session_state.custom_endpoint.strip()
                if provider_info["kind"] == "custom"
                else provider_info.get("endpoint", "")
            )
            if provider_info["kind"] == "custom" and not detect_endpoint:
                st.error("Enter the custom endpoint first.")
            else:
                with st.spinner("Checking available models..."):
                    available = fetch_available_models(
                        provider_info, st.session_state.api_key.strip(), detect_endpoint
                    )
                picked = pick_fallback_model(available, set())
                if picked:
                    st.session_state.model = picked
                    st.success(f"Model set: `{picked}`")
                else:
                    st.error(
                        "Could not get a model list. Check the key/endpoint, "
                        "or type the name yourself in the current model field."
                    )

    st.divider()
    st.header("📁 Project Upload")
    st.caption("Select any files; ZIP files will be extracted automatically.")
    uploaded_files = st.file_uploader(
        "Choose project files or ZIP",
        type=None,
        accept_multiple_files=True,
        help="You can upload individual files, multiple files, or a ZIP.",
    )
    if uploaded_files:
        upload_signature = hashlib.sha256(
            b"".join(uploaded_file.getvalue() for uploaded_file in uploaded_files)
        ).hexdigest()
        if upload_signature != st.session_state.uploaded_signature:
            load_uploaded_files(uploaded_files)
    if st.session_state.project_files:
        st.success(f"{len(st.session_state.project_files)} files ready")
        st.markdown(project_analysis_text(st.session_state.project_analysis))
        with st.expander("View file list"):
            st.text("\n".join(project_file["path"] for project_file in st.session_state.project_files))
        risky_files = [
            project_file["path"]
            for project_file in st.session_state.project_files
            if project_file["path"].lower().endswith(
                (".env", ".pem", ".key", ".p12", ".pfx")
            )
        ]
        if risky_files:
            st.warning(
                "Sensitive-looking files were also loaded. Review before pushing: "
                + ", ".join(risky_files)
            )
        if st.session_state.last_push_report:
            report = st.session_state.last_push_report
            with st.expander("Last GitHub changes — all files"):
                st.markdown(f"**Repository:** `{report['repo']}`")
                for label, key, icon in (
                    ("Created", "created", "✅"),
                    ("Updated", "updated", "🔄"),
                    ("Failed", "failed", "❌"),
                ):
                    paths = report.get(key, [])
                    st.markdown(f"**{label} ({len(paths)})**")
                    if paths:
                        st.text("\n".join(f"{icon} {path}" for path in paths))
                    else:
                        st.caption("None")
        if st.button("Clear uploaded project", use_container_width=True):
            st.session_state.project_files = []
            st.session_state.project_zip_name = None
            st.session_state.uploaded_signature = None
            st.session_state.project_analysis = None
            st.session_state.last_push_report = None
            st.session_state.pending_push = False
            st.session_state.pending_deployment = None
            st.rerun()

    st.divider()
    st.header("🚀 Deployment Control Center")
    st.caption(
        "The Agent will analyze the project, ask for a platform, take your approval before deployment, "
        "deploy Docker to EC2 via AWS SSM, and return the actual live URL + host + port."
    )
    st.selectbox(
        "Select Deployment Platform",
        [ASK_DEPLOYMENT_PLATFORM, *DEPLOYMENT_PLATFORM_OPTIONS],
        key="deployment_platform",
    )
    if st.session_state.deployment_platform == "AWS EC2":
        st.markdown("### 🔐 Secure AWS connection")
        st.caption(
            "The Agent connects to the **user's AWS account**, not the developer's account. "
            "No AWS Access Key, Secret Key, or IAM user is stored in this app."
        )

        with st.expander("🆕 New to AWS? Read this first"):
            st.markdown(
                "**New user? You do not need to know IAM, OIDC, JSON, YAML, roles, or EC2 setup.**\n\n"
                "You only need your own AWS account. The Agent handles the AWS configuration for you."
            )
            st.markdown(
                "**One-time setup:**\n"
                "1. Click **Connect my AWS account** below.\n"
                "2. AWS opens in a new tab — sign in to **your own AWS account**.\n"
                "3. Review the CloudFormation changes and tick AWS's required IAM acknowledgement.\n"
                "4. Click **Create stack**.\n"
                "5. Come back here. The Agent automatically receives the temporary setup result and marks AWS connected.\n\n"
                "You do **not** copy an Account ID or Role ARN, edit a policy, upload a YAML file, or configure an EC2 instance manually."
            )
            st.info(
                "🔒 Security: the Agent never asks for an AWS Access Key or Secret Key. "
                "AWS credentials are short-lived and are used by the GitHub Actions OIDC workflow."
            )

        current_repo = st.session_state.get("current_repo") or st.session_state.get("last_pushed_repo")
        branch_for_setup = "main"
        if current_repo and st.session_state.get("gh_client"):
            try:
                branch_for_setup = st.session_state.gh_client.get_repo(current_repo).default_branch
            except Exception:
                pass

        if not current_repo:
            st.info("Connect GitHub and select/push a repository first. Then the Agent will prepare your AWS connection automatically.")
        else:
            setup_region = (st.session_state.get("aws_region") or "us-east-1").strip()

            automatic_mode = bool(_ENV_AWS_OIDC_TEMPLATE_URL and _ENV_AWS_SETUP_CALLBACK_URL)
            quick_url = None
            if automatic_mode:
                # Generate the temporary setup token once per browser session.
                # It is never an AWS credential and expires server-side.
                if not st.session_state.get("aws_setup_token"):
                    st.session_state.aws_setup_token = _new_aws_setup_token()
                quick_url = aws_quick_create_url(
                    _ENV_AWS_OIDC_TEMPLATE_URL,
                    current_repo,
                    branch_for_setup,
                    setup_region,
                    create_oidc_provider=True,
                    create_ec2_instance=True,
                    setup_token=st.session_state.get("aws_setup_token", ""),
                    callback_url=_ENV_AWS_SETUP_CALLBACK_URL,
                )

                st.markdown("### 🔗 Connect your AWS account")
                st.caption(
                    "One button opens AWS CloudFormation for your own account. "
                    "The Agent supplies the setup token and configuration automatically."
                )
                st.link_button(
                    "🚀 Connect my AWS account",
                    quick_url,
                    use_container_width=True,
                    type="primary",
                )
                st.session_state.aws_setup_pending = True
                st.success(
                    "After you click the button: sign in → acknowledge IAM resource creation → "
                    "Create stack. Then return here; the Agent will connect automatically."
                )
                _render_aws_callback_watcher()

                with st.expander("Already connected / need manual fallback?"):
                    st.caption(
                        "Use this only if the automatic callback cannot be used. "
                        "The normal user flow does not require these fields."
                    )
                    st.text_input(
                        "AWS Account ID or Role ARN",
                        key="aws_account_id_input",
                        placeholder="123456789012 or arn:aws:iam::...:role/...",
                    )
                    st.text_input("AWS Region", key="aws_region", help="Example: ap-south-1")
                    st.text_input(
                        "EC2 Instance ID (optional)",
                        key="aws_ec2_instance_id",
                        placeholder="i-0123456789abcdef0",
                    )
                    if st.button("Connect manually", use_container_width=True, key="aws_manual_connect_auto"):
                        role = aws_role_arn_from_input(st.session_state.get("aws_account_id_input", ""))
                        if not role:
                            st.error("Enter a valid 12-digit AWS Account ID or full Role ARN.")
                        else:
                            st.session_state.aws_oidc_role_arn = role
                            st.session_state.aws_oidc_connected = True
                            st.session_state.aws_setup_pending = False
                            st.success("AWS connected securely.")
                            st.rerun()
            else:
                st.warning(
                    "Automatic AWS onboarding is not configured on this app yet. "
                    "The developer must set **AWS_OIDC_TEMPLATE_URL** and **AWS_SETUP_CALLBACK_URL** once. "
                    "Until then, the manual fallback below remains available."
                )
                st.download_button(
                    "⬇️ Download AWS setup file",
                    data=aws_cloudformation_template(current_repo, branch_for_setup).encode("utf-8"),
                    file_name="github-agent-aws-oidc.yaml",
                    mime="text/yaml",
                    use_container_width=True,
                )
                st.link_button(
                    "⚙️ Open AWS CloudFormation",
                    f"https://{setup_region}.console.aws.amazon.com/cloudformation/home?region={setup_region}#/stacks/create",
                    use_container_width=True,
                )
                st.text_input(
                    "AWS Account ID or Role ARN",
                    key="aws_account_id_input",
                    placeholder="123456789012 or arn:aws:iam::...:role/...",
                )
                st.text_input("AWS Region", key="aws_region", help="Example: ap-south-1")
                st.text_input(
                    "EC2 Instance ID (optional)",
                    key="aws_ec2_instance_id",
                    placeholder="i-0123456789abcdef0",
                )
                if st.button("✅ Connect", use_container_width=True, type="primary", key="aws_manual_connect"):
                    role = aws_role_arn_from_input(st.session_state.get("aws_account_id_input", ""))
                    if not role:
                        st.error("Enter a valid 12-digit AWS Account ID or full Role ARN.")
                    elif not st.session_state.get("gh_user"):
                        st.error("Connect GitHub first.")
                    else:
                        st.session_state.aws_oidc_role_arn = role
                        st.session_state.aws_oidc_connected = True
                        st.success("✅ AWS connected securely. No AWS credential was stored.")
                        st.rerun()

        if _aws_oidc_ready():
            st.success("✅ AWS connected via GitHub OIDC — short-lived credentials are created only inside the GitHub Actions run.")
            if current_repo and st.button("⚙️ Install/update AWS Agent workflow", use_container_width=True):
                try:
                    repo_obj = st.session_state.gh_client.get_repo(current_repo)
                    path = ensure_aws_oidc_workflow(repo_obj, _aws_oidc_role_arn(), repo_obj.default_branch)
                    st.success(f"✅ Installed `{path}`. You can now say: **deploy this project to AWS**.")
                except Exception as exc:
                    st.error(f"Could not install AWS workflow: {exc}")
        st.caption(
            "AWS permissions are limited to EC2 read/deploy, SSM commands, EBS resize, "
            "and deployment-port updates. The Agent does not receive AWS AdministratorAccess."
        )
    elif st.session_state.deployment_platform == "Render":
        _render_key_now = _render_api_key()
        if not _render_key_now:
            st.session_state.render_verified_once = False

        if _render_key_now and st.session_state.get("render_verified_once"):
            st.success(
                "✅ Render connected. Asking to deploy on Render will now redeploy the "
                "linked service through the Render API and return the actual live URL "
                "right here in chat — no manual dashboard hunting needed."
            )
            if st.button("🔁 Render disconnect / reconnect", use_container_width=True):
                st.session_state.render_api_key = ""
                st.session_state.render_verified_once = False
                st.session_state.render_wizard_step = 0
                st.session_state.render_chat_wizard_active = False
                st.rerun()
        elif st.session_state.get("render_chat_wizard_active"):
            st.info("🧭 The Render connect wizard is running in chat — follow the steps in the chat window below ⬇️")
        else:
            st.caption(
                "Render is not connected yet. New user? Use the guided wizard — it walks "
                "through account, GitHub connect, and API key one step at a time, asking "
                "your confirmation before each next step, just like signing up somewhere new."
            )
            if st.button("🔌 Connect Render (guided steps in chat)", use_container_width=True, type="primary"):
                st.session_state.render_chat_wizard_active = True
                st.session_state.render_wizard_step = 0
                st.session_state.messages.append({"role": "user", "content": "connect Render"})
                st.session_state.messages.append({"role": "assistant", "content": render_connect_guidance_reply()})
                st.rerun()

        with st.expander("🔑 Already have a Render API key? Paste it directly"):
            st.text_input(
                "Render API Key",
                key="render_api_key",
                type="password",
                placeholder="rnd_...",
                help="Render → Account Settings → API Keys. Kept only in this browser session, never written to disk.",
            )
            if _render_api_key() and not st.session_state.get("render_verified_once"):
                if st.button("✅ Verify Render key", use_container_width=True, type="primary", key="render_verify_manual"):
                    with st.spinner("Verifying Render API key…"):
                        status = render_connection_status()
                    if status.get("ready"):
                        st.session_state.render_verified_once = True
                        st.session_state.render_chat_wizard_active = False
                        st.success(f"✅ {status.get('message')}")
                        st.rerun()
                    else:
                        st.error(f"Render verification failed: {status.get('message', 'Unknown error')}")

        st.caption(
            "Without any Render API key at all, deploy requests still work — you'll just "
            "get the direct one-click Render setup link instead of an auto-fetched live URL."
        )

        with st.expander("📄 render.yaml in this project"):
            st.markdown(
                "This project ships a `render.yaml` at the repo root so Render uses an "
                "explicit build/start command instead of guessing:\n\n"
                "```yaml\n"
                "buildCommand: pip install -r requirements.txt\n"
                "startCommand: streamlit run app.py --server.port $PORT "
                "--server.address 0.0.0.0 --server.headless true\n"
                "```"
            )
    if st.session_state.project_analysis:
        recommended = st.session_state.project_analysis.get(
            "recommended_platform", "Render"
        )
        st.info(f"File analysis recommendation: **{recommended}**")

    st.divider()
    st.header("🚀 Publish settings")
    st.checkbox("Keep repository private", key="private_repo")
    st.text_input("Commit message", key="commit_message")

st.caption(
    "Chat naturally in English, Hindi, Hinglish, or any language. "
    "You can also just ask to upload the project and publish it to GitHub."
)

def _effective_voice_provider_and_key() -> tuple[str, str]:
    """Resolve which provider/key to use for voice (mic transcription + TTS).

    Preference order:
    1. An explicit voice-only provider/key set in the "Mic / Voice settings" expander.
    2. The main AI provider/key, if that provider happens to be OpenAI or Groq.
    """
    voice_provider = st.session_state.get("voice_provider", "").strip()
    voice_key = st.session_state.get("voice_api_key", "").strip()
    if voice_provider and voice_key:
        return voice_provider, voice_key

    main_provider = st.session_state.get("provider", "")
    main_key = st.session_state.get("api_key", "").strip()
    if main_provider in {"OpenAI", "Groq"} and main_key:
        return main_provider, main_key

    return "", ""


def transcribe_chat_audio(audio_file) -> str:
    """Transcribe audio captured by Streamlit's native chat-input microphone.
    Auto-detects language (English / Hindi / Hinglish).
    """
    if audio_file is None:
        return ""
    provider, api_key = _effective_voice_provider_and_key()
    if not api_key:
        raise RuntimeError(
            "The mic needs an OpenAI or Groq API key. Open '🎤 Mic / "
            "Voice settings' in the sidebar and add a key there (or make "
            "the main AI provider OpenAI/Groq)."
        )

    endpoint = (
        "https://api.openai.com/v1/audio/transcriptions"
        if provider == "OpenAI"
        else "https://api.groq.com/openai/v1/audio/transcriptions"
    )
    model = "whisper-1" if provider == "OpenAI" else "whisper-large-v3-turbo"
    filename = getattr(audio_file, "name", "voice.webm") or "voice.webm"
    mime = getattr(audio_file, "type", None) or "audio/webm"
    payload = audio_file.getvalue() if hasattr(audio_file, "getvalue") else audio_file.read()
    response = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (filename, payload, mime)},
        data={"model": model},
        timeout=120,
    )
    if not response.ok:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except Exception:
            detail = response.text
        raise RuntimeError(f"Voice transcription failed: {detail}")
    text = response.json().get("text", "").strip()
    if not text:
        raise RuntimeError("No speech was detected. Please try speaking again.")
    return text


def _openai_nova_tts(text: str, api_key: str) -> Optional[bytes]:
    """Generate natural speech with OpenAI TTS voice 'nova'."""
    try:
        clean = re.sub(r"[#*`_>]+", " ", str(text))
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) > 2200:
            clean = clean[:2200] + "…"
        if not clean:
            return None
        resp = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "tts-1-hd",
                "input": clean,
                "voice": "nova",
                "response_format": "mp3",
                "speed": 0.92,
            },
            timeout=60,
        )
        if resp.ok and resp.content:
            return resp.content
    except Exception:
        pass
    return None


def render_auto_nova_speak(text: str, key: str) -> None:
    """Silently auto-play Nova voice. No visible Speak/Stop buttons.
    Uses OpenAI 'nova' voice when available, otherwise browser TTS.
    """
    import base64

    safe_text = str(text or "").strip()
    if not safe_text:
        return
    provider, api_key = _effective_voice_provider_and_key()

    audio_b64 = None
    if provider == "OpenAI" and api_key:
        mp3 = _openai_nova_tts(safe_text, api_key)
        if mp3:
            audio_b64 = base64.b64encode(mp3).decode("ascii")

    if audio_b64:
        # Play through the persistent top-level Streamlit page (window.parent),
        # not this throwaway iframe — a brand-new nested iframe has no user-
        # activation of its own and browsers silently block autoplay in it,
        # which is why the Nova voice previously stayed silent.
        components.html(
            f"""
            <script>
              (() => {{
                let speakOn = true;
                try {{
                  const saved = window.parent.localStorage.getItem('nova_speak_replies');
                  speakOn = saved !== 'off';
                }} catch (_) {{}}
                if (!speakOn) return;
                try {{
                  const doc = window.parent.document;
                  let a = doc.getElementById('nova-auto-audio-singleton');
                  if (!a) {{
                    a = doc.createElement('audio');
                    a.id = 'nova-auto-audio-singleton';
                    a.style.display = 'none';
                    doc.body.appendChild(a);
                  }}
                  a.src = 'data:audio/mpeg;base64,{audio_b64}';
                  setTimeout(() => {{
                    a.play().catch((err) => console.log('Nova autoplay blocked:', err));
                  }}, 200);
                }} catch (err) {{
                  console.log('Nova audio setup failed:', err);
                }}
              }})();
            </script>
            """,
            height=0,
        )
    else:
        # Browser TTS fallback, also run against window.parent for the same
        # user-activation reason as above.
        components.html(
            f"""
            <script>
              (() => {{
                const text = {json.dumps(safe_text, ensure_ascii=False)};
                let win;
                try {{ win = window.parent; }} catch (_) {{ win = window; }}
                if (!win || !('speechSynthesis' in win) || !text) return;
                try {{
                  const saved = win.localStorage.getItem('nova_speak_replies');
                  if (saved === 'off') return;
                }} catch (_) {{}}
                const scoreVoice = (v) => {{
                  const n = v.name.toLowerCase();
                  let score = 0;
                  if (/neural|natural|premium|enhanced|wavenet/.test(n)) score += 10;
                  if (/google/.test(n)) score += 4;
                  if (/microsoft/.test(n) && !/desktop/.test(n)) score += 5;
                  if (v.localService === false) score += 2;
                  if (/^en|^hi/.test(v.lang)) score += 1;
                  return score;
                }};
                const chooseVoice = () => {{
                  const voices = win.speechSynthesis.getVoices() || [];
                  if (!voices.length) return null;
                  let saved = null;
                  try {{ saved = win.localStorage.getItem('nova_voice_name'); }} catch (_) {{}}
                  const byName = saved && voices.find(v => v.name === saved);
                  if (byName) return byName;
                  return [...voices].sort((a, b) => scoreVoice(b) - scoreVoice(a))[0] || null;
                }};
                const speak = () => {{
                  win.speechSynthesis.cancel();
                  const u = new win.SpeechSynthesisUtterance(text);
                  const v = chooseVoice();
                  if (v) u.voice = v;
                  u.rate = 1.02;
                  u.pitch = 1.0;
                  win.speechSynthesis.speak(u);
                }};
                if (win.speechSynthesis.getVoices().length === 0) {{
                  win.speechSynthesis.onvoiceschanged = speak;
                }}
                setTimeout(speak, 350);
              }})();
            </script>
            """,
            height=0,
        )


def render_live_mic_panel() -> None:
    """Free, browser-native live voice mic — ported from the web app's design:
    a real-time pulsing circular waveform, Web Speech API SpeechRecognition
    for speech-to-text (no OpenAI/Groq key needed at all), auto-send on the
    final transcript, and a smart "best available voice" picker for
    text-to-speech (SpeechSynthesis), with a dropdown so the user can
    override the pick — same working rule as the web app's mic panel.

    It reaches into the parent Streamlit page (components.html renders a
    same-origin iframe) to type the transcript into the native chat input
    and submit it, so no extra Python-side wiring or custom component
    build is needed.
    """
    components.html(
        """
        <style>
          html, body { margin:0; padding:0; background:transparent; }
          #nova-mic-wrap * { box-sizing:border-box; }
        </style>
        <div id="nova-mic-wrap" style="
            font-family:'Plus Jakarta Sans',system-ui,sans-serif;
            background:linear-gradient(135deg,rgba(139,123,247,.14),rgba(45,212,191,.10));
            border:1px solid rgba(148,142,255,.28);
            border-radius:16px;
            padding:10px 14px 12px 14px;
            margin:0;
        ">
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="position:relative;width:44px;height:44px;flex:0 0 auto;">
              <canvas id="novaMicCanvas" width="44" height="44" style="position:absolute;inset:0;"></canvas>
              <button id="novaMicBtn" type="button" title="Click and speak" style="
                position:absolute;inset:0;width:44px;height:44px;border-radius:50%;
                border:none;cursor:pointer;font-size:18px;
                background:linear-gradient(135deg,#8b7bf7,#2dd4bf);color:#fff;
                display:flex;align-items:center;justify-content:center;
                box-shadow:0 4px 14px -4px rgba(91,76,210,.6);
              ">🎤</button>
            </div>
            <div style="flex:1;min-width:0;">
              <div style="
                font-weight:700;font-size:13.5px;letter-spacing:.2px;
                background:linear-gradient(90deg,#8b7bf7,#2dd4bf);
                -webkit-background-clip:text;background-clip:text;color:transparent;
              ">Live AI Nova Assistant</div>
              <div id="novaMicLabel" style="font-size:12px;color:#6b6489;margin-top:2px;line-height:1.35;">
                Mic pe click karo, bolo — transcript apne aap chat box mein jaa kar bhej diya jayega.
              </div>
            </div>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px;flex-wrap:wrap;">
            <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#6b6489;cursor:pointer;user-select:none;">
              <span style="position:relative;display:inline-block;width:34px;height:18px;flex:0 0 auto;">
                <input id="novaSpeakToggle" type="checkbox" checked style="opacity:0;width:0;height:0;">
                <span id="novaSpeakTrack" style="
                  position:absolute;inset:0;border-radius:999px;cursor:pointer;
                  background:linear-gradient(135deg,#8b7bf7,#2dd4bf);
                  transition:background .15s;
                "></span>
                <span id="novaSpeakKnob" style="
                  position:absolute;top:2px;left:18px;width:14px;height:14px;border-radius:50%;
                  background:#fff;transition:left .15s;box-shadow:0 1px 3px rgba(0,0,0,.3);
                "></span>
              </span>
              Speak replies
            </label>
            <select id="novaVoiceSelect" title="Nova ki awaaz" style="
              max-width:190px;font-size:12px;padding:4px 6px;border-radius:8px;
              border:1px solid rgba(148,142,255,.35);background:rgba(255,255,255,.6);
            "></select>
          </div>
          <div style="display:flex;align-items:center;gap:8px;margin-top:10px;">
            <input id="novaTextInput" type="text" placeholder="Type or press the mic to speak…" style="
              flex:1;min-width:0;font-size:13.5px;padding:9px 12px;border-radius:12px;
              border:1px solid rgba(148,142,255,.35);background:rgba(255,255,255,.85);
              color:#1b1b2f;outline:none;font-family:inherit;
            ">
            <button id="novaSendBtn" type="button" style="
              flex:0 0 auto;font-size:13.5px;font-weight:600;padding:9px 18px;border-radius:12px;
              border:none;cursor:pointer;color:#fff;font-family:inherit;
              background:linear-gradient(135deg,#8b7bf7,#6a5cff);
              box-shadow:0 4px 14px -4px rgba(91,76,210,.6);
            ">Send</button>
          </div>
        </div>
        <script>
        (() => {
          const parentDoc = window.parent.document;
          const micBtn = document.getElementById('novaMicBtn');
          const micCanvas = document.getElementById('novaMicCanvas');
          const micCtx = micCanvas.getContext('2d');
          const micLabel = document.getElementById('novaMicLabel');
          const voiceSelect = document.getElementById('novaVoiceSelect');
          const textInput = document.getElementById('novaTextInput');
          const sendBtn = document.getElementById('novaSendBtn');
          const speakToggle = document.getElementById('novaSpeakToggle');
          const speakKnob = document.getElementById('novaSpeakKnob');
          const speakTrack = document.getElementById('novaSpeakTrack');
          const DEFAULT_LABEL = micLabel.textContent;

          // ---- Reach into the real Streamlit chat input and submit it ----
          function findChatTextarea() {
            return parentDoc.querySelector('textarea[data-testid="stChatInputTextArea"]')
                || parentDoc.querySelector('[data-testid="stChatInput"] textarea')
                || parentDoc.querySelector('textarea[placeholder*="Mic"]')
                || parentDoc.querySelector('textarea');
          }
          function sendToStreamlit(text) {
            const textarea = findChatTextarea();
            if (!textarea || !text) return false;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            setter.call(textarea, text);
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            textarea.focus();
            setTimeout(() => {
              textarea.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
              }));
            }, 80);
            return true;
          }

          // ---- Typed text: Send button / Enter key ----
          function sendTyped() {
            const value = textInput.value.trim();
            if (!value) return;
            sendToStreamlit(value);
            textInput.value = '';
          }
          sendBtn.addEventListener('click', sendTyped);
          textInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              sendTyped();
            }
          });

          // ---- "Speak replies" toggle – shared with render_auto_nova_speak ----
          function applySpeakToggleUI(on) {
            speakTrack.style.background = on
              ? 'linear-gradient(135deg,#8b7bf7,#2dd4bf)'
              : 'rgba(148,142,255,.35)';
            speakKnob.style.left = on ? '18px' : '2px';
          }
          let speakOn = true;
          try {
            const saved = window.parent.localStorage.getItem('nova_speak_replies');
            speakOn = saved !== 'off';
          } catch (_) {}
          speakToggle.checked = speakOn;
          applySpeakToggleUI(speakOn);
          speakToggle.addEventListener('change', () => {
            applySpeakToggleUI(speakToggle.checked);
            try {
              window.parent.localStorage.setItem('nova_speak_replies', speakToggle.checked ? 'on' : 'off');
            } catch (_) {}
          });

          // ---- Speech-to-text: free, browser-native, no API key ----
          const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
          let recognizer = null;
          let listening = false;
          if (SpeechRecognition) {
            recognizer = new SpeechRecognition();
            recognizer.continuous = false;
            recognizer.interimResults = true;
            recognizer.lang = 'en-IN';
            recognizer.onresult = (event) => {
              let finalTranscript = '';
              let interim = '';
              for (let i = event.resultIndex; i < event.results.length; i++) {
                const t = event.results[i][0].transcript;
                if (event.results[i].isFinal) finalTranscript += t;
                else interim += t;
              }
              if (finalTranscript) {
                micLabel.textContent = finalTranscript;
                sendToStreamlit(finalTranscript);
              } else if (interim) {
                micLabel.textContent = interim + ' …';
              }
            };
            recognizer.onend = () => stopListeningUI();
            recognizer.onerror = () => stopListeningUI();
          } else {
            micBtn.title = "Voice input isn't supported in this browser — try Chrome or Edge.";
            micBtn.style.opacity = '0.4';
            micLabel.textContent = 'Browser voice input available nahi hai — Chrome/Edge try karo.';
          }

          let audioCtx, analyser, dataArray, rafId, mediaStream;

          async function startListeningUI() {
            listening = true;
            micBtn.style.background = 'linear-gradient(135deg,#ff9a44,#ff6f5b)';
            micLabel.textContent = 'Listening…';
            try {
              mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
              audioCtx = new (window.AudioContext || window.webkitAudioContext)();
              const source = audioCtx.createMediaStreamSource(mediaStream);
              analyser = audioCtx.createAnalyser();
              analyser.fftSize = 256;
              source.connect(analyser);
              dataArray = new Uint8Array(analyser.frequencyBinCount);
              drawWaveform();
            } catch (_) {
              /* mic permission denied — recognition may still work without visualizer */
            }
          }

          function stopListeningUI() {
            listening = false;
            micBtn.style.background = 'linear-gradient(135deg,#8b7bf7,#2dd4bf)';
            micLabel.textContent = DEFAULT_LABEL;
            if (rafId) cancelAnimationFrame(rafId);
            micCtx.clearRect(0, 0, micCanvas.width, micCanvas.height);
            if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
            if (audioCtx) audioCtx.close();
          }

          function drawWaveform() {
            rafId = requestAnimationFrame(drawWaveform);
            analyser.getByteFrequencyData(dataArray);
            const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
            const radius = 16 + Math.min(avg / 5, 6);
            micCtx.clearRect(0, 0, micCanvas.width, micCanvas.height);
            micCtx.beginPath();
            micCtx.arc(22, 22, radius, 0, Math.PI * 2);
            micCtx.fillStyle = 'rgba(255,154,68,0.18)';
            micCtx.fill();
            micCtx.beginPath();
            micCtx.arc(22, 22, radius * 0.6, 0, Math.PI * 2);
            micCtx.fillStyle = 'rgba(255,154,68,0.32)';
            micCtx.fill();
          }

          micBtn.addEventListener('click', () => {
            if (!recognizer) return;
            if (listening) {
              recognizer.stop();
              stopListeningUI();
            } else {
              startListeningUI();
              try { recognizer.start(); } catch (_) { /* already started */ }
            }
          });

          // ---- Text-to-speech voice picker: same scoring rule as the web app ----
          function scoreVoice(v) {
            const n = v.name.toLowerCase();
            let score = 0;
            if (/neural|natural|premium|enhanced|wavenet/.test(n)) score += 10;
            if (/google/.test(n)) score += 4;
            if (/microsoft/.test(n) && !/desktop/.test(n)) score += 5;
            if (v.localService === false) score += 2;
            if (/^en|^hi/.test(v.lang)) score += 1;
            return score;
          }
          // Curated shortlist: 5 female + 5 male "Online (Natural)" Microsoft voices.
          // Only voices whose short name is in this list are shown, so the
          // dropdown never has more than 10 entries (when available on the
          // browser/OS). Swap any first name below to change who's on the list.
          const NOVA_FEMALE_VOICES = ['sonia', 'libby', 'neerja', 'natasha', 'clara'];
          const NOVA_MALE_VOICES   = ['ryan', 'thomas', 'prabhat', 'liam', 'sam'];
          const NOVA_ALLOWED_VOICES = [...NOVA_FEMALE_VOICES, ...NOVA_MALE_VOICES];
          function isAllowedVoice(v) {
            const n = v.name.toLowerCase();
            if (!/online/.test(n) || /desktop/.test(n)) return false;
            return NOVA_ALLOWED_VOICES.some((short) => n.includes(short));
          }
          function loadVoices() {
            const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
            if (!voices.length) return;
            let shortlisted = voices.filter(isAllowedVoice);
            // Fallback: if none of the curated names exist on this system,
            // don't leave the dropdown empty — show the best 10 available.
            const pool = shortlisted.length ? shortlisted : voices;
            const sorted = [...pool].sort((a, b) => scoreVoice(b) - scoreVoice(a)).slice(0, 10);
            voiceSelect.innerHTML = '';
            sorted.forEach((v) => {
              const opt = document.createElement('option');
              opt.value = v.name;
              opt.textContent = v.name + ' (' + v.lang + ')';
              voiceSelect.appendChild(opt);
            });
            const saved = window.parent.localStorage.getItem('nova_voice_name');
            const preferred = sorted.find((v) => v.name === saved) || sorted[0];
            if (preferred) voiceSelect.value = preferred.name;
            window.parent.localStorage.setItem('nova_voice_name', voiceSelect.value);
          }
          if (window.speechSynthesis) {
            loadVoices();
            window.speechSynthesis.onvoiceschanged = loadVoices;
          }
          voiceSelect.addEventListener('change', () => {
            window.parent.localStorage.setItem('nova_voice_name', voiceSelect.value);
          });
        })();
        </script>
        """,
        height=215,
    )


# Backward-compatible alias — older code in this file calls render_live_mic_panel().
render_live_voice_mic = render_live_mic_panel


def render_nova_voice(text: str, key: str, auto_play: bool = False, prefer_natural: bool = True) -> None:
    """Compatibility wrapper – now silent auto-play only."""
    if auto_play or prefer_natural:
        render_auto_nova_speak(text, key)


render_voice_controls = render_nova_voice


status_col1, status_col2, status_col3 = st.columns(3)
with status_col1:
    st.metric("GitHub", "Connected" if st.session_state.gh_user else "Not connected")
with status_col2:
    st.metric("AI", st.session_state.provider if st.session_state.api_key else "API key needed")
with status_col3:
    st.metric("Project", f"{len(st.session_state.project_files)} files" if st.session_state.project_files else "Not uploaded")

chat_col, action_col = st.columns([1, 1], gap="large")

with chat_col:
    st.subheader("💬 Agent Chat · Live AI Nova")
    st.caption("🎤 Click the icon and speak — the transcript will be sent automatically (free, no API key) · Nova's voice will play automatically on reply")

    # Show only the messages – NO Speak/Stop buttons under each reply.
    #
    # Nova's voice is triggered from *here* — once the page has settled after
    # a rerun — rather than right when the answer is first generated. Firing
    # it earlier (immediately before st.rerun()) was a race: st.rerun() tears
    # that component down before the browser gets a real chance to start
    # playback, so the audio silently never played. Triggering it on the
    # stable history render below fixes that.
    st.session_state.setdefault("nova_last_spoken_idx", -1)
    last_idx = len(st.session_state.messages) - 1
    for idx, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
        if (
            message["role"] == "assistant"
            and idx == last_idx
            and idx > st.session_state.nova_last_spoken_idx
        ):
            render_auto_nova_speak(message["content"], f"speak-{idx}")
            st.session_state.nova_last_spoken_idx = idx

    if st.session_state.get("aws_chat_wizard_active") and not _aws_oidc_ready():
        with st.chat_message("assistant"):
            render_aws_connect_wizard_step()

    if st.session_state.get("render_chat_wizard_active") and not (
        _render_api_key() and st.session_state.get("render_verified_once")
    ):
        with st.chat_message("assistant"):
            render_connect_wizard_step()

    # Clean Live AI Nova status banner (no button here — the real mic lives
    # inside the chat box below, via Streamlit's own native audio recorder).
    #
    # NOTE: the native st.chat_input's own attach-file button is invisible —
    # see the CSS above that hides [data-testid="stChatInput"] completely,
    # because this custom Nova panel replaces it visually. So a *separate*,
    # actually-visible uploader lives here instead. Selecting a file here
    # does not touch the sidebar's full-project upload/push at all — it only
    # feeds gh_update_files_subset() below, which updates just this file.
    st.session_state.setdefault("chat_attach_uploader_version", 0)
    chat_uploaded_files = st.file_uploader(
        "📎 Attach a file (only this file will be updated/committed to the GitHub repo)",
        accept_multiple_files=True,
        key=f"chat_attach_uploader_{st.session_state.chat_attach_uploader_version}",
    )
    if chat_uploaded_files:
        st.caption(
            f"📎 {len(chat_uploaded_files)} file"
            f"{'s' if len(chat_uploaded_files) != 1 else ''} ready — now type your "
            "message below (or send it with Send/mic without any text)."
        )
    render_live_mic_panel()

    # Always auto-play Nova voice – no checkbox needed
    st.session_state.auto_speak = True

    chat_value = st.chat_input(
        "Speak into the mic, type, or attach a file with 📎 above…",
        accept_audio=True,
        accept_file="multiple",
        key="agent_chat_input",
    )
    if chat_value:
        user_text = chat_value if isinstance(chat_value, str) else getattr(chat_value, "text", "") or ""
        audio_value = None if isinstance(chat_value, str) else getattr(chat_value, "audio", None)
        chat_files = [] if isinstance(chat_value, str) else list(getattr(chat_value, "files", None) or [])
        attached_files: List[Dict[str, Any]] = []
        seen_paths = set()
        for chat_file in list(chat_uploaded_files or []) + chat_files:
            try:
                safe_name = safe_zip_path(chat_file.name) or chat_file.name
                if safe_name in seen_paths:
                    continue
                seen_paths.add(safe_name)
                attached_files.append({"path": safe_name, "content": chat_file.getvalue()})
            except Exception:
                continue
        mic_feedback: Optional[str] = None
        if audio_value is not None and not user_text.strip():
            with st.spinner("🎤 Listening…"):
                try:
                    user_text = transcribe_chat_audio(audio_value)
                except Exception as exc:
                    user_text = ""
                    mic_feedback = str(exc)
        elif audio_value is None and not user_text.strip() and not attached_files:
            # Mic was opened/closed but no audio and no text ever reached us —
            # this used to fail completely silently. Tell the user clearly.
            mic_feedback = (
                "Nothing was recorded. Click the mic 🎤 icon, allow the browser "
                "mic permission, say something, then click ✓ (send)."
            )
        if attached_files and not user_text.strip():
            # File(s) attached via 📎 with no typed message — still a valid
            # submission, default to a clear intent so the single-file
            # update flow in handle_message kicks in.
            names = ", ".join(item["path"] for item in attached_files)
            user_text = f"update this file: {names}"
        if mic_feedback:
            st.markdown(
                f"""
                <div style="
                    background:linear-gradient(135deg,rgba(255,111,181,.16),rgba(124,92,255,.14));
                    border:1px solid rgba(124,92,255,.35);border-radius:14px;
                    padding:10px 14px;margin:6px 0 4px 0;color:var(--text-0,#1b1b2f);
                    font-size:13.5px;display:flex;gap:8px;align-items:flex-start;">
                    <span style="font-size:16px;">🎙️</span><span>{html.escape(mic_feedback)}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        if user_text and user_text.strip():
            user_text = user_text.strip()
            display_text = user_text
            if attached_files:
                display_text += "\n\n📎 " + ", ".join(
                    f"`{item['path']}`" for item in attached_files
                )
            st.session_state.messages.append({"role": "user", "content": display_text})
            with st.chat_message("user"):
                st.markdown(display_text)
            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    answer = handle_message(user_text, attached_files=attached_files or None)
                st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
            if attached_files:
                # Reset the uploader (new widget key) so this file isn't
                # silently re-attached to the person's next, unrelated message.
                st.session_state.chat_attach_uploader_version += 1
            # Don't speak here — st.rerun() immediately below would tear this
            # component down before the browser can start playing audio in
            # it. The rerun redraws this same message from history further
            # up in this file, and Nova's voice is triggered there instead,
            # once, after the page has actually settled.
            st.rerun()

with action_col:
    st.subheader("⚡ Live Action Workspace")
    ghv_tab, timeline_tab = st.tabs(["🖥️ Live GitHub View", "⚡ Action Timeline"])

    with ghv_tab:
        st.caption(
            "Whatever action the agent performs on GitHub — opening a repo, settings, "
            "issues, releases, workflows — a screen that looks just like real "
            "GitHub updates here instantly."
        )
        render_live_github_view()

    with timeline_tab:
        pending = st.session_state.pending_confirmation
        if pending:
            st.warning("User approval required before execution.")
            st.markdown(approval_description(pending))
            st.caption("Current token status: GitHub will enforce the actual permission at execution time.")
            b1, b2, b3 = st.columns(3)
            if b1.button("✅ Approve", key="live_approve", use_container_width=True):
                st.session_state.pending_confirmation = None
                emit_action_event("approval_received", "approved", "Approval received",
                                  "User approved the action.", pending)
                with st.spinner("Executing approved action..."):
                    result = execute_action(pending)
                st.session_state.action_history.append({
                    "action_id": st.session_state.active_action_id,
                    "type": pending.get("type"),
                    "status": "completed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                st.session_state.messages.append({"role":"assistant","content":str(result)})
                st.rerun()
            if b2.button("⛔ Deny", key="live_deny", use_container_width=True):
                st.session_state.pending_confirmation = None
                emit_action_event("approval_denied", "denied", "Approval denied",
                                  "User denied the action.", pending)
                st.session_state.action_history.append({
                    "action_id": st.session_state.active_action_id,
                    "type": pending.get("type"),
                    "status": "denied",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                st.rerun()
            if b3.button("✖ Cancel Action", key="live_cancel", use_container_width=True):
                st.session_state.pending_confirmation = None
                emit_action_event("action_cancelled", "cancelled", "Action cancelled",
                                  "User cancelled the action.", pending)
                st.rerun()
        else:
            st.info("No action is waiting for approval.")

        events = st.session_state.action_events[-30:]
        if events:
            completed = sum(1 for e in events if e["status"] == "completed")
            st.progress(min(1.0, completed / max(1, len(events))))
            st.markdown("**Step timeline**")
            for e in reversed(events):
                icon = {"completed":"🟢","running":"🔵","waiting":"🟡",
                        "failed":"🔴","denied":"⛔","cancelled":"⚪"}.get(e["status"],"•")
                st.markdown(f"{icon} **{e['title']}** — {e['message']}")
                if e.get("data"):
                    with st.expander("Event details", expanded=False):
                        st.json(e["data"])

        if st.session_state.action_history:
            with st.expander("Action history", expanded=False):
                st.json(st.session_state.action_history[-20:])
        if st.button("🔄 Refresh / Verify", use_container_width=True):
            st.rerun()
