import os
from pathlib import Path

from flask import Flask, abort, send_from_directory
from werkzeug.exceptions import HTTPException

ROOT_DIR = Path(__file__).resolve().parent.parent
WEBSITE_DIR = ROOT_DIR / "apps" / "website"

# The pages reach above `apps/website` for shared images such as
# `workflows/TrackFlowLogo.png`, which is all the repository-root fallback is
# for. The root also holds `.env`, `data/` and the source code, so the fallback
# serves images and nothing else.
ROOT_ASSET_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"})

app = Flask(__name__, static_folder=None)


def _is_public_root_asset(asset_path: str) -> bool:
    path = Path(asset_path)
    if any(part.startswith(".") for part in path.parts):
        return False
    return path.suffix.lower() in ROOT_ASSET_SUFFIXES


@app.get("/")
def index():
    return send_from_directory(WEBSITE_DIR, "index.html")


@app.get("/<path:asset_path>")
def static_assets(asset_path: str):
    website_candidate = WEBSITE_DIR / asset_path
    if website_candidate.is_file():
        return send_from_directory(WEBSITE_DIR, asset_path)

    root_candidate = ROOT_DIR / asset_path
    if _is_public_root_asset(asset_path) and root_candidate.is_file():
        return send_from_directory(ROOT_DIR, asset_path)

    abort(404)


# Every error leaves as the same small JSON body. It names the status and says
# what to do next, and never echoes the requested path or anything about the
# server behind it.
_ERROR_MESSAGES = {
    404: "There is nothing at this address. Go back to the homepage at /.",
    405: "This page cannot be submitted to. Go back to the homepage at /.",
}
_SERVER_ERROR_MESSAGE = (
    "Something went wrong on our side. Please try again, and contact TrackFlow Tech "
    "if the problem continues."
)


@app.errorhandler(HTTPException)
def http_error(error: HTTPException):
    status = error.code or 500
    message = _ERROR_MESSAGES.get(status, _SERVER_ERROR_MESSAGE if status >= 500 else error.name)
    return {"error": error.name, "message": message}, status


@app.errorhandler(Exception)
def unhandled_error(error: Exception):
    app.logger.exception("Unhandled exception while serving the static site", exc_info=error)
    return {"error": "Internal Server Error", "message": _SERVER_ERROR_MESSAGE}, 500


if __name__ == "__main__":
    # The Werkzeug debugger shows tracebacks and an interactive console to
    # whoever triggers an error, and this binds to every interface. Opt in
    # explicitly with FLASK_DEBUG=1 on a trusted machine.
    app.run(host="0.0.0.0", port=5500, debug=os.getenv("FLASK_DEBUG") == "1")
