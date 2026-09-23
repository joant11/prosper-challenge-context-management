#
# Voice Agent Builder — the Phase 1 web app.
#
#   Agent CRUD (save/load graphs by id)  +  WebRTC test-call signaling.
#
# The test call reuses bot.py's own pipeline (`run_bot`, `transport_params`)
# rather than re-implementing it: this endpoint's only job is to pick which
# agent JSON a given call should run, wire up the WebRTC connection via the
# same SmallWebRTCRequestHandler the Pipecat dev runner uses, and hand off.
#

import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pipecat.runner.types import SmallWebRTCRunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

import agent_store
from agent_builder import AgentBuilder, ValidationIssue, validate_config
from agent_builder.schema import AgentConfig
from bot import run_bot, transport_params

load_dotenv(Path(__file__).parent / ".env", override=True)

app = FastAPI(title="Voice Agent Builder")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

_webrtc_handler = SmallWebRTCRequestHandler()


# ---- agent CRUD -------------------------------------------------------------


@app.get("/api/agents")
async def list_agents():
    return agent_store.list_agents()


@app.get("/api/templates")
async def list_templates():
    return [{"id": key, "name": data["name"]} for key, data in agent_store.TEMPLATES.items()]


@app.post("/api/agents")
async def create_agent(request: Request):
    body = await request.json() if await request.body() else {}
    try:
        agent_id, data = agent_store.create(
            name=body.get("name"), template=body.get("template", "blank")
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": agent_id, "config": data}


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    try:
        return agent_store.load_raw(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Agent not found")


@app.put("/api/agents/{agent_id}")
async def put_agent(agent_id: str, request: Request):
    data = await request.json()
    try:
        agent_store.save(agent_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    warnings = [i for i in validate_config(AgentConfig.from_dict(data)) if i.severity == "warning"]
    return {"id": agent_id, "warnings": [_issue_dict(w) for w in warnings]}


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str):
    agent_store.delete(agent_id)
    return {"status": "deleted"}


@app.get("/api/agents/{agent_id}/validate")
async def validate_agent(agent_id: str):
    try:
        config = agent_store.load_config(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except Exception as e:
        return {"errors": [{"message": str(e), "node": None}], "warnings": []}
    issues = validate_config(config)
    return {
        "errors": [_issue_dict(i) for i in issues if i.severity == "error"],
        "warnings": [_issue_dict(i) for i in issues if i.severity == "warning"],
    }


def _issue_dict(issue: ValidationIssue) -> dict:
    return {"message": issue.message, "node": issue.node}


# ---- test call (WebRTC signaling) -------------------------------------------


@app.post("/api/offer")
async def offer(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    webrtc_request = SmallWebRTCRequest.from_dict(payload)
    request_data = webrtc_request.request_data or {}
    agent_id = request_data.get("agent_id") if isinstance(request_data, dict) else None

    if not agent_id:
        raise HTTPException(status_code=400, detail="request_data.agent_id is required")

    try:
        config = agent_store.load_config(agent_id)
        builder = AgentBuilder(config)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    except ValueError as e:
        # Fatal validation error — refuse to even open the connection.
        raise HTTPException(status_code=400, detail=f"Agent does not compile: {e}")

    async def on_connection(connection: SmallWebRTCConnection):
        runner_args = SmallWebRTCRunnerArguments(
            webrtc_connection=connection,
            body=request_data,
            session_id=str(uuid.uuid4()),
        )
        transport = await create_transport(runner_args, transport_params)
        background_tasks.add_task(run_bot, transport, runner_args, builder)

    try:
        answer = await _webrtc_handler.handle_web_request(webrtc_request, on_connection)
    except Exception as e:
        logger.error(f"WebRTC offer failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return answer


@app.patch("/api/offer")
async def offer_patch(request: Request):
    payload = await request.json()
    patch_request = SmallWebRTCPatchRequest(
        pc_id=payload["pc_id"],
        candidates=[IceCandidate(**c) for c in payload.get("candidates", [])],
    )
    await _webrtc_handler.handle_patch_request(patch_request)
    return {"status": "success"}


# ---- frontend ----------------------------------------------------------------

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
