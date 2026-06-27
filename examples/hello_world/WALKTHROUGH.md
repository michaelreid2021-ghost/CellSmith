# Walkthrough: From "Hello, world!" to surgical LLM patch

This is the smallest possible end-to-end tour of CellSmith. You'll annotate a
trivial file, ask any LLM to make it absurd, and apply the patch.

The whole point: **you never explain the patch schema in your prompt.** The
embedded `# %% [ai_schema:instructions]` block does that for you. You only
write the natural-language ask.

---

## 0. Install

```bash
pip install -e .
```

## 1. Annotate

```bash
cellsmith annotate examples/hello_world/hello.py
```

Open `hello.py` and you'll see the file now opens with an `[ai_schema:...]`
block (the embedded instructions to the model) plus cell markers around each
function:

```python
# %% [ai_schema:instructions]
# AI INSTRUCTIONS - PATCH SCHEMA:
# ...
# %% [ai_schema:end]

# %% [func:greet]
def greet(name):
    return f"Hello, {name}!"


# %% [func:main]
def main():
    print(greet("world"))


if __name__ == "__main__":
    main()
```

Those `func:greet` / `func:main` strings are the cell IDs the model will
target.

## 2. Ask any LLM (chat UI is fine)

Paste the **whole annotated file** into Claude / ChatGPT / Gemini / your local
qwen-coder / whatever. Then your *only* prompt is:

> Make this the most complex Hello World you can possibly imagine. Be
> ridiculous. Use the patch schema embedded in the file.

That's it. No schema explanation, no role prompting, no JSON examples — the
file already taught the model what to return.

## 3. Save the response

The model returns a JSON object that looks like:

```json
{
  "revisions": [
    {
      "filename": "examples/hello_world/hello.py",
      "revision_type": "CELL_PATCH",
      "cell_id": "func:greet",
      "code_content": "# %% [func:greet]\ndef greet(name):\n    ...absurd implementation...\n"
    }
  ],
  "changelog": [
    {
      "change_type": "new_feature",
      "summary": "greet() now sings the user's name in three-part harmony with ANSI color."
    }
  ]
}
```

Note the **`changelog` block is required** — `cellsmith patch` rejects any
payload without it. The embedded schema in the annotated file already tells
the model this, so you don't need to remind it.

Save the response as `patch.json` in the repo root.

## 4. Apply

```bash
cellsmith patch patch.json .
```

Run it:

```bash
python examples/hello_world/hello.py
```

If the LLM did its job, you now have a Hello World that involves
threading, ASCII art, recursion, or some kind of cosmic horror. If it broke,
roll back:

```bash
cellsmith rollback patch.json .
```

After `cellsmith patch` runs you'll also have a fresh entry appended to
`CHANGELOG.cellsmith.jsonl` at the repo root — your structured project
history, one JSON object per line.

## 5. Clean up

When you want the file back to plain Python:

```bash
cellsmith strip examples/hello_world/hello.py
```

Strips both the AI schema header and all `# %% [...]` markers (asks for
confirmation; pass `-y` to skip). Want only one or the other:

```bash
cellsmith strip hello.py --prompt-only    # keep cell markers, drop the schema
cellsmith strip hello.py --markers-only   # keep schema, drop cell markers
```

---

**That's the whole loop.** Annotate, paste, ask in plain English, apply.
The LLM never had to be told the JSON shape — the file taught it.
