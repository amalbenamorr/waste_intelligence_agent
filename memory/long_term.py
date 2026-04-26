# ============================================================
# memory/long_term.py — v12
#
# FIXES vs v11:
# 1. persist_directory ABSOLU basé sur le répertoire du projet
#    (plus de chemin relatif qui change selon le répertoire d'exécution)
# 2. ChromaDB PersistentClient utilisé (remplace le client ephémère)
# 3. Vérification que la collection existe et contient des données au démarrage
# 4. get_similar retourne des résultats même si la collection est vide (pas d'exception)
# 5. save() avec métadonnées complètes et timestamp
# 6. Log explicite du chemin ChromaDB pour debug
# ============================================================

import os
import uuid
from datetime import datetime
from pathlib import Path


def _get_chroma_path() -> str:
    """
    v12: Compute ABSOLUTE path for ChromaDB storage.
    Uses the project root (parent of memory/ directory).
    """
    # This file is at: <project_root>/memory/long_term.py
    this_file   = Path(__file__).resolve()
    project_root = this_file.parent.parent          # <project_root>/
    chroma_path  = project_root / "outputs" / "chromadb"
    chroma_path.mkdir(parents=True, exist_ok=True)
    return str(chroma_path)


CHROMA_PATH = _get_chroma_path()


class LongTermMemory:
    """
    v12: Long-term memory using ChromaDB with guaranteed persistence.
    Uses absolute paths and PersistentClient.
    """

    COLLECTION_NAME = "waste_analyses"

    def __init__(self):
        self._client     = None
        self._collection = None
        self._available  = False
        self._init()

    def _init(self):
        """Initialize ChromaDB with PersistentClient."""
        print(f"  [LongTermMemory] ChromaDB path: {CHROMA_PATH}")
        try:
            import chromadb

            # v12: use PersistentClient for guaranteed disk persistence
            self._client = chromadb.PersistentClient(path=CHROMA_PATH)

            # Get or create collection
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )

            count = self._collection.count()
            print(f"  [LongTermMemory]  Collection '{self.COLLECTION_NAME}' ready "
                  f"({count} documents stored)")
            self._available = True

        except ImportError:
            print("  [LongTermMemory]  chromadb not installed → memory disabled")
            print("   Run: pip install chromadb")
            self._available = False
        except Exception as e:
            print(f"  [LongTermMemory]  Init failed: {e}")
            self._available = False

    def save(
        self,
        description: str,
        risk: str        = "unknown",
        product: str     = "unknown",
        report_summary:  str = "",
    ) -> str:
        """
        Save an analysis to long-term memory.
        Returns the analysis ID.
        """
        if not self._available or not self._collection:
            print("  [LongTermMemory] Memory not available — skipping save")
            return "memory_unavailable"

        if not description or len(description.strip()) < 5:
            print("  [LongTermMemory] Description too short — skipping save")
            return "description_too_short"

        analysis_id = str(uuid.uuid4())[:8]
        timestamp   = datetime.now().isoformat()

        # Build the document text for embedding
        doc_text = (
            f"Description: {description}\n"
            f"Risk: {risk}\n"
            f"Product: {product}\n"
            f"Summary: {report_summary}\n"
            f"Date: {timestamp}"
        )

        metadata = {
            "risk":           risk,
            "product":        product,
            "report_summary": report_summary[:500] if report_summary else "",
            "timestamp":      timestamp,
            "date":           datetime.now().strftime("%Y-%m-%d"),
        }

        try:
            self._collection.add(
                documents=[doc_text],
                metadatas=[metadata],
                ids=[analysis_id],
            )
            count = self._collection.count()
            print(f"  [LongTermMemory]  Saved ID={analysis_id} | "
                  f"Total in DB: {count} | Path: {CHROMA_PATH}")
            return analysis_id

        except Exception as e:
            print(f"  [LongTermMemory]  Save failed: {e}")
            return f"save_error_{str(e)[:30]}"

    def get_similar(self, query: str, n: int = 2) -> list:
        """
        Retrieve similar past analyses from long-term memory.
        Returns list of dicts with analysis data, or empty list if none found.
        """
        if not self._available or not self._collection:
            return []

        try:
            count = self._collection.count()
            if count == 0:
                print("  [LongTermMemory] No past analyses in DB yet")
                return []

            # Query the collection
            n_results = min(n, count)
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
            )

            similar = []
            if results and results.get("documents"):
                docs      = results["documents"][0]
                metadatas = results.get("metadatas", [[]])[0]
                distances = results.get("distances", [[]])[0]

                for i, doc in enumerate(docs):
                    meta = metadatas[i] if i < len(metadatas) else {}
                    dist = distances[i]  if i < len(distances)  else 1.0
                    similarity = round(1.0 - dist, 3)

                    similar.append({
                        "document":   doc,
                        "risk":       meta.get("risk",    "unknown"),
                        "product":    meta.get("product", "unknown"),
                        "summary":    meta.get("report_summary", ""),
                        "date":       meta.get("date",    ""),
                        "similarity": similarity,
                    })

            print(f"  [LongTermMemory] Found {len(similar)} similar analyses "
                  f"(query='{query[:40]}...')")
            return similar

        except Exception as e:
            print(f"  [LongTermMemory]  get_similar failed: {e}")
            return []

    def list_all(self) -> list:
        """List all stored analyses — useful for debugging."""
        if not self._available or not self._collection:
            return []
        try:
            results = self._collection.get()
            items = []
            for i, doc_id in enumerate(results.get("ids", [])):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                items.append({
                    "id":      doc_id,
                    "risk":    meta.get("risk",    ""),
                    "product": meta.get("product", ""),
                    "date":    meta.get("date",    ""),
                })
            return items
        except Exception as e:
            print(f"  [LongTermMemory] list_all failed: {e}")
            return []

    def count(self) -> int:
        """Return number of stored analyses."""
        if not self._available or not self._collection:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    def clear(self):
        """Clear all stored analyses — use with caution."""
        if not self._available or not self._client:
            return
        try:
            self._client.delete_collection(self.COLLECTION_NAME)
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            print("  [LongTermMemory]  Collection cleared")
        except Exception as e:
            print(f"  [LongTermMemory] clear failed: {e}")