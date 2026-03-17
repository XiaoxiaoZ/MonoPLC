"""
MonoPLC Bridge Server — LLM Supervisory Layer Decision Endpoints

Core Constraints:
  - LLM is supervisory layer, not control layer
  - Whitelist mechanism: Only allow {0,1,2,5,6,7,9} type Effects
  - Prohibit direct control of actuators (EFF_VALVE_CTRL) or triggering alarms (EFF_ALARM)
"""

import json
import logging

import httpx
from fastapi import APIRouter, HTTPException

from config import settings
from models import (
    Effect,
    EffectType,
    LLM_ALLOWED_EFFECTS,
    LLMActionResponse,
    LLMChatRequest,
    LLMDecisionResponse,
    LLMInterpretationResponse,
    StatusCard,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm", tags=["LLM"])

# ---------------------------------------------------------------------------
# System Prompt — Explicitly tell LLM it is the supervisory layer
# ---------------------------------------------------------------------------

async def get_system_prompt() -> str:
    """Dynamically generate System Prompt, fetch operation document based on config.py's SYS_DOC_URL."""
    
    # 1. Fetch dynamic operation document
    sys_doc_content = "No system operation document available."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(settings.SYS_DOC_URL)
            if resp.status_code == 200:
                sys_doc_content = resp.text
            else:
                logger.warning(f"Failed to fetch system doc: HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"Exception fetching system doc: {e}")

    # 2. Dynamically generate whitelist and enum definitions
    allowed_list = []
    forbidden_list = []
    
    for e in EffectType:
        if e.value in LLM_ALLOWED_EFFECTS:
            allowed_list.append(f"- {e.name} ({e.value})")
        else:
            forbidden_list.append(f"- {e.name} ({e.value})")
            
    allowed_str = "\n".join(allowed_list)
    forbidden_str = "\n".join(forbidden_list)

    return f"""You are the intelligent supervisory layer decision engine of an industrial automation system.
You are **NOT** the control layer — the closed-loop control of physical actuators is completed in real-time by the underlying PLC.
Your responsibility is: issue **operator-level** decisions based on real-time system state and user commands.

Below is the dynamically injected "System Operation Manual", please strictly follow its semantics to understand the meaning of various parameters:
================= System Operation Manual Start =================
{sys_doc_content}
================= System Operation Manual End =================

Below are the operational constraints you must follow:
Action commands you can use (Whitelist):
{allowed_str}

Types you are **PROHIBITED** from using (belongs to control layer or internal system commands):
{forbidden_str}

If the user asks you to perform a prohibited operation, explicitly refuse and explain the reason in reasoning, and suggest a whitelisted action that can achieve a similar purpose.

Your output must perfectly match the following JSON format (return only JSON, no markdown):
{{
  "e_type": 0,
  "target": "Target variable name (max 32 chars)",
  "payload": "String payload (max 64 chars)",
  "value": 0.0,
  "reasoning": "Brief explanation of the decision"
}}
"""


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

async def _call_ollama(user_prompt: str) -> dict:
    """Call Ollama API, returning parsed JSON."""
    url = f"{settings.OLLAMA_URL}/api/generate"
    
    prompt_str = await get_system_prompt()
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json={
            "model": settings.OLLAMA_MODEL,
            "prompt": user_prompt,
            "system": prompt_str,
            "format": "json",
            "stream": False,
        })
        response.raise_for_status()

    result = response.json()
    # Ollama return format: {"response": "...", ...}
    raw_text = result.get("response", "{}")
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("Ollama returned invalid JSON: %s", raw_text)
        return {"e_type": 0, "reasoning": "LLM returned invalid format"}


def _build_state_prompt(state: dict, user_input: str | None = None) -> str:
    """Build user prompt for Ollama (dynamic state serialization, no hardcoded domain logic)."""
    lines = ["Current system state (JSON mapping):"]
    
    # Generic state dumping
    for key, value in state.items():
        if isinstance(value, dict) or isinstance(value, list):
            val_str = json.dumps(value, ensure_ascii=False)
        else:
            val_str = str(value)
        lines.append(f"- {key}: {val_str}")
        
    lines.append("")
    if user_input:
        lines.append(f"User natural language command: {user_input}")
    else:
        lines.append("No external user command (system routine auto-inspection)")
        
    lines.append("\nPlease combine the above state information and decide the next move. Strictly follow the JSON Schema to return.")
    return "\n".join(lines)


def _validate_and_respond(llm_result: dict) -> LLMDecisionResponse:
    """Whitelist validation for LLM returned Effect."""
    e_type_val = llm_result.get("e_type", 0)
    reasoning = llm_result.get("reasoning", "")

    try:
        e_type = EffectType(e_type_val)
    except ValueError:
        return LLMDecisionResponse(
            effect=Effect(),
            reasoning=reasoning,
            allowed=False,
            message=f"LLM returned unknown e_type: {e_type_val}",
        )

    effect = Effect(
        e_type=e_type,
        target=llm_result.get("target", "")[:32],
        payload=llm_result.get("payload", "")[:64],
        value=llm_result.get("value", 0.0),
    )

    # Whitelist validation
    if int(e_type) not in LLM_ALLOWED_EFFECTS:
        return LLMDecisionResponse(
            effect=effect,
            reasoning=reasoning,
            allowed=False,
            message=f"❌ {e_type.name} rejected by whitelist. Valves and alarms are managed by PLC real-time control layer.",
        )

    return LLMDecisionResponse(
        effect=effect,
        reasoning=reasoning,
        allowed=True,
        message="✅ Decision passed whitelist validation",
    )


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@router.post("/decide", summary="LLM pure state-driven decision", response_model=LLMDecisionResponse)
async def llm_decide():
    """
    Read reconstructed state from StateStore → Ollama auto-decision.
    Write to PLC after passing whitelist validation.
    """
    from main import app_state

    state = app_state.state_store.get_state()
    prompt = _build_state_prompt(state)

    try:
        llm_result = await _call_ollama(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama call failed: {exc}")

    response = _validate_and_respond(llm_result)

    # Passed whitelist → Write to PLC
    if response.allowed and response.effect.e_type != EffectType.EFF_NONE:
        app_state.plc_bridge.push_input_effect(response.effect)

    return response


@router.post("/chat", summary="User text input driving LLM decision", response_model=LLMDecisionResponse)
async def llm_chat(req: LLMChatRequest):
    """
    Receive user natural language command → send to Ollama along with StateStore state → whitelist validation → write to PLC.
    """
    from main import app_state

    state = app_state.state_store.get_state()
    prompt = _build_state_prompt(state, user_input=req.user_input)

    try:
        llm_result = await _call_ollama(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama call failed: {exc}")

    response = _validate_and_respond(llm_result)

    # Passed whitelist → Write to PLC
    if response.allowed and response.effect.e_type != EffectType.EFF_NONE:
        app_state.plc_bridge.push_input_effect(response.effect)

    return response


@router.get("/interpret", summary="LLM Real-time Interpretation of Monoid State", response_model=LLMInterpretationResponse)
async def llm_interpret():
    """
    Read current system state and rolling logs, let LLM interpret them anthropomorphically, and return statements to be displayed on the dashboard.
    """
    from main import app_state

    state = app_state.state_store.get_state()
    logs = app_state.state_store.get_logs(10)  # Get latest 10 logs

    # 1. Assemble state string for LLM input
    lines = ["Below is the real-time underlying state snapshot of the industrial automation system. As an intelligent analyst, please provide an easy-to-understand report for the operator."]
    lines.append("\n[Current System Core Scalar State]")
    for key, val in state.items():
        if key != "logs":
            lines.append(f"- {key}: {val}")
            
    lines.append("\n[Recent Monoid Event Logs (Reverse chronological, latest is most important)]")
    for log in reversed(logs):
        lines.append(f"[{log['timestamp']}] {log['e_type']} (target={log['target']}, value={log['value']}, payload={log['payload']})")

    user_prompt = "\n".join(lines)

    system_prompt = f"""You are the "Real-time Interpreter" of the industrial automation system.
Your sole job is to translate obscure PLC state variables and Monoid Effect type streams into monitoring commentary that human operators can easily understand.

Constraints:
1. Do not give advice on how to process code or repair machines, you are only responsible for "stating what is happening".
2. Crush those enum types (e.g. EFF_VALVE_CTRL) and specific pin names, and report them in an anthropomorphic and colloquial way (e.g.: "Just now the system opened the cooling valve because the temperature was too high").
3. If the system has been in the same state and nothing new happened, you can say "Currently the system is running smoothly, all indicators are normal".
4. You also decide WHAT status items to show on the operator dashboard via status_cards. Pick the most important current readings and states.
5. The output must perfectly match the following JSON format (return only JSON, no markdown):
{{
  "summary": "One-sentence system health summary",
  "details": "Detailed natural language interpretation of what is happening",
  "status_cards": [
    {{"label": "Temperature", "value": "75.3°C", "status": "normal"}},
    {{"label": "Cooling Valve", "value": "OPEN", "status": "active"}},
    {{"label": "System Alarm", "value": "CLEAR", "status": "normal"}}
  ]
}}
Status field must be one of: "normal", "warning", "critical", "active", "inactive".
"""

    url = f"{settings.OLLAMA_URL}/api/generate"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json={
                "model": settings.OLLAMA_MODEL,
                "prompt": user_prompt,
                "system": system_prompt,
                "format": "json",
                "stream": False,
            })
            response.raise_for_status()
            
        result = response.json()
        raw_text = result.get("response", "{}")
        logger.info(f"Ollama raw interpretation output: {raw_text}")
        
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Failed to parse Ollama output as JSON")
            parsed = {}
        
        # Parse status cards from LLM output
        raw_cards = parsed.get("status_cards", [])
        status_cards = []
        if isinstance(raw_cards, list):
            for card in raw_cards:
                if isinstance(card, dict) and "label" in card and "value" in card:
                    status_cards.append(StatusCard(
                        label=card.get("label", ""),
                        value=card.get("value", ""),
                        status=card.get("status", "normal"),
                    ))

        return LLMInterpretationResponse(
            summary=parsed.get("summary", "Interpretation generation failed or return format incorrect"),
            details=parsed.get("details", ""),
            status_cards=status_cards,
        )
    except Exception as exc:
        logger.error(f"LLM dynamic interpretation failed: {exc}")
        return LLMInterpretationResponse(
            summary="LLM query timed out or failed",
            details="Backend could not contact the large language model, please check Ollama connection or retry.",
        )
