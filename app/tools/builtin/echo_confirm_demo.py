from pydantic import BaseModel

from app.tools.registry import Tool


class EchoInput(BaseModel):
    text: str


async def echo_confirm_demo(inp: EchoInput) -> dict[str, str]:
    return {"echoed": inp.text}


TOOL = Tool(
    "echo_confirm_demo",
    "Demo tool requiring user confirmation; echoes the given text.",
    EchoInput,
    "confirm",
    echo_confirm_demo,
)
