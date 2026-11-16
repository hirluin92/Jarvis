from pydantic import BaseModel, Field

from app.db import session as db
from app.db.models import Note
from app.tools.registry import Tool


class NoteInput(BaseModel):
    text: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)


async def create_note(inp: NoteInput) -> dict[str, object]:
    async with db.session_maker()() as s:
        note = Note(title=inp.title, body=inp.text)
        s.add(note)
        await s.commit()
        note_id = note.id
    async with db.session_maker()() as s:  # read back: the proof is what is really stored
        saved = await s.get(Note, note_id)
    if saved is None:
        raise RuntimeError(f"note {note_id} not found after saving")
    return {"id": saved.id, "title": saved.title, "chars": len(saved.body)}


TOOL = Tool(
    "create_note",
    "Save a note (free text, optional title) in the personal notes. Reversible.",
    NoteInput,
    "write",
    create_note,
)
