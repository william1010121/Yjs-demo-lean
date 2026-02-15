import asyncio
import json
import os
import logging
from pathlib import Path
from urllib.parse import urlparse

from hypercorn import Config
from hypercorn.asyncio import serve
from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute
from starlette.responses import FileResponse, JSONResponse
from starlette.websockets import WebSocketDisconnect

try:
    from pycrdt_websocket import ASGIServer, WebsocketServer, YRoom
    from pycrdt_websocket.ystore import FileYStore, YDocNotFound
except ImportError:
    from pycrdt.websocket import ASGIServer, WebsocketServer, YRoom
    from pycrdt.store import FileYStore, YDocNotFound

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lean project directory (where lake serve runs)
# ---------------------------------------------------------------------------
LEAN_PROJECT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lean-project")
LEAN_FILE_PATH = os.path.join(LEAN_PROJECT_DIR, "src", "Scratch.lean")
LEAN_PROJECT_URI = Path(LEAN_PROJECT_DIR).resolve().as_uri()
LEAN_FILE_URI = Path(LEAN_FILE_PATH).resolve().as_uri()


# ---------------------------------------------------------------------------
# Yjs WebSocket Server (reused from reference)
# ---------------------------------------------------------------------------
class Server(WebsocketServer):
    async def get_room(self, name: str) -> YRoom:
        if name not in self.rooms:
            ystore = FileYStore(path=f"./data/{name}.ystore")
            room = YRoom(ystore=ystore, ready=False)
            self.rooms[name] = room

        room = self.rooms[name]
        await self.start_room(room)

        if room.ystore is not None and not room.ready:
            try:
                await room.ystore.apply_updates(room.ydoc)
            except YDocNotFound:
                pass
            room.ready = True

        return room


# ---------------------------------------------------------------------------
# LSP Process Manager — one `lake serve` per session
# ---------------------------------------------------------------------------
class LeanProcessManager:
    def __init__(self):
        self.processes: dict[str, asyncio.subprocess.Process] = {}

    async def spawn(self, session_id: str) -> asyncio.subprocess.Process:
        if session_id in self.processes:
            proc = self.processes[session_id]
            if proc.returncode is None:
                return proc
            # Process died, remove it
            del self.processes[session_id]

        proc = await asyncio.create_subprocess_exec(
            "lake", "serve",
            cwd=LEAN_PROJECT_DIR,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.processes[session_id] = proc
        logger.info(f"Spawned lake serve for session {session_id} (pid={proc.pid})")
        return proc

    async def kill(self, session_id: str):
        proc = self.processes.pop(session_id, None)
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
            logger.info(f"Killed lake serve for session {session_id}")

    async def kill_all(self):
        for sid in list(self.processes):
            await self.kill(sid)


# ---------------------------------------------------------------------------
# LSP Content-Length framing helpers
# ---------------------------------------------------------------------------
def encode_lsp_message(obj: dict) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


async def read_lsp_message(stdout: asyncio.StreamReader) -> dict | None:
    """Read one LSP message from stdout using Content-Length framing."""
    content_length = -1
    while True:
        line = await stdout.readline()
        if not line:
            return None  # EOF
        line_str = line.decode("ascii", errors="replace").strip()
        if not line_str:
            break  # empty line = end of headers
        if line_str.lower().startswith("content-length:"):
            content_length = int(line_str.split(":", 1)[1].strip())

    if content_length < 0:
        return None

    body = await stdout.readexactly(content_length)
    return json.loads(body)


# ---------------------------------------------------------------------------
# Global process manager
# ---------------------------------------------------------------------------
lean_manager = LeanProcessManager()


def _write_lean_file(content: str):
    """Write editor content to the Lean source file on disk."""
    try:
        with open(LEAN_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Failed to write {LEAN_FILE_PATH}: {e}")


def _is_valid_file_uri(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    if parsed.scheme != "file":
        return False
    return bool(parsed.path)


def _validate_lsp_message_uris(msg: dict) -> list[str]:
    """Validate URI fields in LSP payloads before forwarding to Lean."""
    errors: list[str] = []
    params = msg.get("params")
    if not isinstance(params, dict):
        return errors

    method = msg.get("method")

    if method == "initialize":
        root_uri = params.get("rootUri")
        if not _is_valid_file_uri(root_uri):
            errors.append(f"initialize.params.rootUri={root_uri!r}")

    text_document = params.get("textDocument")
    uri = text_document.get("uri") if isinstance(text_document, dict) else None
    methods_requiring_doc_uri = {
        "textDocument/didOpen",
        "textDocument/didChange",
        "textDocument/hover",
        "textDocument/completion",
        "$/lean/plainGoal",
        "$/lean/plainTermGoal",
    }
    if method in methods_requiring_doc_uri and not _is_valid_file_uri(uri):
        errors.append(f"{method}.params.textDocument.uri={uri!r}")
    elif uri is not None and not _is_valid_file_uri(uri):
        errors.append(f"{method}.params.textDocument.uri={uri!r}")

    return errors


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(ws_server: Server):
    ws_asgi = ASGIServer(ws_server)
    base_dir = os.path.dirname(os.path.abspath(__file__))

    async def homepage(request):
        return FileResponse(os.path.join(base_dir, "index.html"))

    async def file_uri(request):
        return JSONResponse({
            "fileUri": LEAN_FILE_URI,
            "rootUri": LEAN_PROJECT_URI,
        })

    async def yjs_ws_handler(websocket):
        scope = websocket.scope
        await ws_asgi(scope, websocket._receive, websocket._send)

    async def lsp_ws_handler(websocket):
        session_id = websocket.path_params["session_id"]
        await websocket.accept()

        try:
            proc = await lean_manager.spawn(session_id)
        except Exception as e:
            logger.error(f"Failed to spawn lake serve: {e}")
            await websocket.close(code=1011, reason=str(e))
            return

        async def ws_to_stdin():
            """Forward WebSocket messages → lake serve stdin."""
            try:
                while True:
                    data = await websocket.receive_text()
                    msg = json.loads(data)
                    uri_errors = _validate_lsp_message_uris(msg)
                    if uri_errors:
                        logger.error(
                            "Rejecting invalid LSP payload (session=%s, errors=%s, payload=%s)",
                            session_id,
                            "; ".join(uri_errors),
                            json.dumps(msg, ensure_ascii=False),
                        )
                        await websocket.close(code=1011, reason="Invalid LSP URI")
                        return
                    # Intercept didOpen / didChange to sync content to disk
                    method = msg.get("method", "")
                    if method == "textDocument/didOpen":
                        text = msg.get("params", {}).get("textDocument", {}).get("text")
                        if text is not None:
                            _write_lean_file(text)
                    elif method == "textDocument/didChange":
                        changes = msg.get("params", {}).get("contentChanges", [])
                        if changes:
                            # Full-document sync: take the last full text
                            text = changes[-1].get("text")
                            if text is not None:
                                _write_lean_file(text)
                    proc.stdin.write(encode_lsp_message(msg))
                    await proc.stdin.drain()
            except (WebSocketDisconnect, RuntimeError):
                pass
            except Exception:
                logger.exception("Unexpected error forwarding WS -> lake stdin (session=%s)", session_id)

        async def stdout_to_ws():
            """Forward lake serve stdout → WebSocket."""
            try:
                while True:
                    msg = await read_lsp_message(proc.stdout)
                    if msg is None:
                        break
                    await websocket.send_text(json.dumps(msg))
            except (WebSocketDisconnect, RuntimeError, ConnectionError):
                pass

        async def log_stderr():
            """Log stderr from lake serve."""
            try:
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    logger.info(f"[lake-{session_id}] {line.decode(errors='replace').rstrip()}")
            except Exception:
                pass

        tasks = [
            asyncio.create_task(ws_to_stdin()),
            asyncio.create_task(stdout_to_ws()),
            asyncio.create_task(log_stderr()),
        ]

        try:
            # Wait for any task to finish (disconnect or process exit)
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        finally:
            await lean_manager.kill(session_id)
            try:
                await websocket.close()
            except Exception:
                pass

    app = Starlette(
        routes=[
            Route("/", homepage),
            Route("/file-uri", file_uri),
            WebSocketRoute("/yjs/{room}", yjs_ws_handler),
            WebSocketRoute("/lsp/{session_id}", lsp_ws_handler),
        ]
    )
    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Lean 4 Collaborative Editor Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    os.makedirs("./data", exist_ok=True)

    server = Server(rooms_ready=False)
    app = create_app(server)

    config = Config()
    config.bind = [f"{args.host}:{args.port}"]

    print(f"Lean project dir: {LEAN_PROJECT_DIR}")
    print(
        "Startup /file-uri payload:",
        json.dumps(
            {
                "fileUri": LEAN_FILE_URI,
                "rootUri": LEAN_PROJECT_URI,
            },
            ensure_ascii=False,
        ),
    )
    print(f"Server running at: http://{args.host}:{args.port}")
    print(f"Yjs WebSocket:     ws://{args.host}:{args.port}/yjs/{{room}}")
    print(f"LSP WebSocket:     ws://{args.host}:{args.port}/lsp/{{session_id}}")

    async with server:
        try:
            await serve(app, config, mode="asgi")
        finally:
            await lean_manager.kill_all()


if __name__ == "__main__":
    asyncio.run(main())
