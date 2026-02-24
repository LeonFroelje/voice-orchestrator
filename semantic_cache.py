import json
import logging
import boto3
import botocore.exceptions
import numpy as np
from sentence_transformers import SentenceTransformer
from config import settings

logger = logging.getLogger("SemanticCache")


class S3SemanticCache:
    def __init__(
        self,
        exact_tools: set,
        bucket_name: str = settings.s3_bucket,
        object_key: str = "tool_cache.json",
    ):
        self.exact_tools = exact_tools
        self.bucket = bucket_name
        self.key = object_key

        # Structure: {"mach das licht aus": {"tool": "control_light", "args": {"action": "turn_off"}, "exact_only": False}}
        self.cache_dict = {}
        self.utterance_matrix = None
        self.utterance_texts = []

        logger.info("Initializing Semantic Tool Cache...")
        try:
            self.encoder = SentenceTransformer(
                settings.embedding_model, local_files_only=True
            )
        except Exception:
            self.encoder = SentenceTransformer(settings.embedding_model)

        self.s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name="garage",
            config=boto3.session.Config(signature_version="s3v4"),
        )

        self._load_from_s3()

    def _load_from_s3(self):
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=self.key)
            self.cache_dict = json.loads(response["Body"].read().decode("utf-8"))
            logger.info(f"Loaded {len(self.cache_dict)} cached commands from S3.")

            # --- SELF HEALING: Update old cache flags to match current tools.json ---
            cache_changed = False
            for text, data in self.cache_dict.items():
                should_be_exact = data["tool"] in self.exact_tools
                if data.get("exact_only") != should_be_exact:
                    self.cache_dict[text]["exact_only"] = should_be_exact
                    cache_changed = True

            if cache_changed:
                logger.info(
                    "Updated stale exact_only flags in cache to match current registry."
                )
                self._sync_to_s3()

        except botocore.exceptions.ClientError:
            logger.warning("No tool_cache.json found. Starting fresh.")
            self._sync_to_s3()

        self._rebuild_matrix()

    def _rebuild_matrix(self):
        # ONLY include texts that are allowed to be fuzzy matched!
        self.utterance_texts = [
            text
            for text, data in self.cache_dict.items()
            if not data.get("exact_only", False)
        ]

        if not self.utterance_texts:
            self.utterance_matrix = None
            return

        embeddings = self.encoder.encode(self.utterance_texts)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.clip(norms, a_min=1e-9, a_max=None)
        self.utterance_matrix = embeddings / norms

    def _sync_to_s3(self):
        json_data = json.dumps(self.cache_dict, indent=2, ensure_ascii=False)
        self.s3_client.put_object(
            Bucket=self.bucket, Key=self.key, Body=json_data.encode("utf-8")
        )

    def add_to_cache(self, utterance: str, tool_name: str, tool_args: dict):
        utterance = utterance.lower().strip()

        # Check against our dynamic registry passed from main.py
        exact_only = tool_name in self.exact_tools

        if utterance not in self.cache_dict:
            self.cache_dict[utterance] = {
                "tool": tool_name,
                "args": tool_args,
                "exact_only": exact_only,
            }
            logger.info(
                f"Learned new phrase for cache: '{utterance}' -> {tool_name} (Exact Only: {exact_only})"
            )

            # Only rebuild the matrix if a new FUZZY command was added
            if not exact_only:
                self._rebuild_matrix()

            self._sync_to_s3()

    def get_cached_tool(
        self, query: str, threshold: float = 0.92
    ) -> tuple[str, dict, float]:
        """Returns (tool_name, tool_args, confidence_score)"""
        query = query.lower().strip()

        # --- TIER 1: Exact Match (Lightning Fast, 100% Safe for Variables) ---
        if query in self.cache_dict:
            cached_data = self.cache_dict[query]
            return cached_data["tool"], cached_data["args"], 1.0

        # --- TIER 2: Semantic Match (Fuzzy Matching) ---
        if self.utterance_matrix is None:
            return None, None, 0.0

        query_vector = self.encoder.encode([query])[0]
        norm = max(np.linalg.norm(query_vector), 1e-9)
        query_normalized = query_vector / norm

        similarities = np.dot(self.utterance_matrix, query_normalized)
        best_idx = np.argmax(similarities)
        best_score = float(similarities[best_idx])

        if best_score >= threshold:
            matched_text = self.utterance_texts[best_idx]
            cached_data = self.cache_dict[matched_text]

            # Defensive guardrail: Double check we aren't returning an exact_only tool
            if not cached_data.get("exact_only", False):
                logger.debug(
                    f"Semantic match found: '{query}' matched '{matched_text}' ({best_score:.2f})"
                )
                return cached_data["tool"], cached_data["args"], best_score

        return None, None, best_score
