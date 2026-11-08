"""Lea's documents (knowledge/docs_index.py): Office files, folders to skip, opening one by name."""

import zipfile

from core.db import Database
from knowledge.docs_index import DocsIndex, read_file
from knowledge.embeddings import HashEmbedder


def docx(path, *paras):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return path


def pptx(path, *slides):
    with zipfile.ZipFile(path, "w") as z:
        for i, text in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>")
    return path


def test_office_files_are_read(tmp_path):
    assert read_file(docx(tmp_path / "e.docx", "Teorías del delito", "Beccaria &amp; Lombroso")) == [
        ("", "Teorías del delito\nBeccaria & Lombroso\n")
    ]
    slides = read_file(pptx(tmp_path / "s.pptx", "Intro", "Conclusión"))
    assert slides == [("slide 1", "Intro\n"), ("slide 2", "Conclusión\n")]


def test_folders_skip_code_and_find_by_name(tmp_path):
    docs = tmp_path / "Documents"
    (docs / "Uni").mkdir(parents=True)
    docx(docs / "Uni" / "Ensayo criminología final.docx", "La teoría de la anomia de Merton explica…")
    (docs / "Projects" / "app" / ".git").mkdir(parents=True)
    (docs / "Projects" / "app" / "notes.md").write_text("code project notes")
    idx = DocsIndex(Database(tmp_path / "d.db", dim=64), HashEmbedder(64))
    counts = idx.index_folders([str(docs)])
    assert counts["files"] == 1  # the .git project is skipped
    path = idx.find("ensayo criminologia")[0]
    assert path.endswith("Ensayo criminología final.docx") and "anomia de Merton" in idx.text_of(path)
    assert idx.find("física") == []


def test_read_document_tool(tmp_path):
    from tests.test_orchestrator import make, run

    call = "<tool_call>\n<function=read_document>\n<parameter=name>\nensayo de criminologia\n</parameter>\n</function>\n</tool_call>"

    s = make(tmp_path, [])
    docx(tmp_path / "Ensayo criminología.docx", "La anomia de Merton.")
    s.docs.index_file(tmp_path / "Ensayo criminología.docx")
    s.llm.replies = [
        call,
        "Tu ensayo trata la anomia de Merton.",
        "{}",
    ]
    ev = run(s, "Resume mi ensayo de criminología")
    tool = [m["content"] for m in s.llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "anomia de Merton" in tool and tool.startswith(
        '<untrusted source="document Ensayo criminología.docx">'
    )
    assert any(e["type"] == "sources" for e in ev)
