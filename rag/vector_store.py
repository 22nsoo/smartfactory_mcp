from __future__ import annotations

import hashlib
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

from rag.embeddings import HashingEmbeddings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
DEFAULT_PERSIST_DIR = PROJECT_ROOT / "data/vector_db"
COLLECTION_NAME = "smart_factory_maintenance"


def split_markdown(path: Path) -> list[Document]:
    text = path.read_text(encoding="utf-8").strip()
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and current:
            blocks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [
        Document(
            page_content=block,
            metadata={
                "source": path.name,
                "chunk": index,
                "title": next((line.lstrip("# ") for line in block.splitlines() if line.startswith("#")), path.stem),
            },
        )
        for index, block in enumerate(blocks)
        if block
    ]


def load_documents(knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> list[Document]:
    documents: list[Document] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        documents.extend(split_markdown(path))
    if not documents:
        raise FileNotFoundError(f"No knowledge documents found in {knowledge_dir}")
    return documents


def document_id(document: Document) -> str:
    value = f"{document.metadata['source']}:{document.metadata['chunk']}:{document.page_content}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def open_vector_store(persist_dir: Path = DEFAULT_PERSIST_DIR) -> Chroma:
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=HashingEmbeddings(),
        persist_directory=str(persist_dir),
        collection_metadata={"hnsw:space": "cosine"},
    )


def index_documents(
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
    persist_dir: Path = DEFAULT_PERSIST_DIR,
) -> dict:
    documents = load_documents(knowledge_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)
    store = open_vector_store(persist_dir)
    ids = [document_id(document) for document in documents]
    store.add_documents(documents, ids=ids)
    sources = sorted({str(document.metadata["source"]) for document in documents})
    return {
        "collection": COLLECTION_NAME,
        "document_count": len(documents),
        "source_count": len(sources),
        "sources": sources,
        "persist_directory": str(persist_dir),
        "embedding": "HashingVectorizer char_wb 2-5 grams, 768 dimensions",
    }
