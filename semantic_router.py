import json
import logging
import boto3
import botocore.exceptions
import numpy as np
from sentence_transformers import SentenceTransformer

from config import settings

logger = logging.getLogger("SemanticRouter")


class S3SemanticRouter:
    def __init__(
        self, bucket_name: str = settings.s3_bucket, object_key: str = "routes.json"
    ):
        logger.info("Initializing Semantic Router (Loading all-MiniLM-L6-v2)...")
        self.encoder = SentenceTransformer(settings.embedding_model)
        self.threshold = 0.55  # Cosine similarity threshold

        self.s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name="garage",
            config=boto3.session.Config(signature_version="s3v4"),
        )

        self.bucket = bucket_name
        self.key = object_key

        self.routes = []
        self.utterance_matrix = None
        self.utterance_routes = []

        self.route_dict = {
            "media": [
                "spiele musik im wohnzimmer",
                "mach das radio an",
                "nächstes lied bitte",
                "musik stoppen",
                "setze die lautstärke auf 50 prozent",
                "was läuft gerade",
                "leere die warteschlange",
                "musik fortsetzen",
            ],
            "timers": [
                "stell einen timer auf 5 minuten",
                "wecke mich in 10 minuten",
                "wie viel zeit ist noch auf dem timer",
                "timer abbrechen",
                "stoppe den alarm",
            ],
            "home_control": [
                "mach das licht in der küche an",
                "licht aus",
                "stell die heizung auf 22 grad",
                "aktiviere die szene schlafen",
                "mir ist kalt",
                "Deckenleuchte Büro aus",
                "Schreibtischlampe aus",
                "Tischlampe aus",
                "Lichtekette aus",
                "Lichterkette an",
                "Thermostat auf 20 Grad",
                "dimme das licht",
            ],
        }

        self._load_from_s3()

    def _load_from_s3(self):
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=self.key)
            self.route_dict = json.loads(response["Body"].read().decode("utf-8"))
            logger.info("Successfully loaded routing intelligence from S3.")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.warning(
                    "No routes.json found in S3. Using default routes and creating file."
                )
                self._sync_to_s3()
            else:
                logger.error(f"S3 Error loading routes: {e}")
        except json.JSONDecodeError as e:
            logger.error(
                f"S3 routes.json is corrupted: {e}. Falling back to default routes."
            )

        # Batch encode everything efficiently
        all_embeddings = []

        for route_name, utterances in self.route_dict.items():
            if not utterances:
                continue

            if route_name not in self.routes:
                self.routes.append(route_name)

            embeddings = self.encoder.encode(utterances)

            # Safe normalization
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.clip(norms, a_min=1e-9, a_max=None)
            normalized_embeddings = embeddings / norms

            all_embeddings.append(normalized_embeddings)
            self.utterance_routes.extend([route_name] * len(utterances))

        # Stack the matrix exactly ONCE
        if all_embeddings:
            self.utterance_matrix = np.vstack(all_embeddings)

    def _sync_to_s3(self):
        json_data = json.dumps(self.route_dict, indent=2, ensure_ascii=False)
        self.s3_client.put_object(
            Bucket=self.bucket, Key=self.key, Body=json_data.encode("utf-8")
        )

    def learn_new_phrase(self, route_name: str, utterance: str):
        route_name = route_name.lower().strip()
        utterance = utterance.lower().strip()

        # Check for cross-route conflicts
        for existing_route, phrases in self.route_dict.items():
            if utterance in phrases:
                if existing_route == route_name:
                    return f"Ich weiß bereits, dass '{utterance}' zu '{route_name}' gehört."
                else:
                    return f"Konflikt: '{utterance}' löst bereits die Kategorie '{existing_route}' aus."

        if route_name not in self.route_dict:
            self.route_dict[route_name] = []
            self.routes.append(route_name)

        self.route_dict[route_name].append(utterance)

        # Safe normalization for the single new phrase
        new_embedding = self.encoder.encode([utterance])
        norm = np.linalg.norm(new_embedding, axis=1, keepdims=True)
        norm = np.clip(norm, a_min=1e-9, a_max=None)
        normalized = new_embedding / norm

        # Dynamically append to the existing matrix
        if self.utterance_matrix is None:
            self.utterance_matrix = normalized
        else:
            self.utterance_matrix = np.vstack((self.utterance_matrix, normalized))

        self.utterance_routes.append(route_name)
        self._sync_to_s3()

        return f"Okay, ich habe gelernt, dass '{utterance}' die Kategorie '{route_name}' auslöst."

    def get_route(self, query: str) -> str:
        if self.utterance_matrix is None:
            logger.debug("No utterance matrix")
            return None

        query_vector = self.encoder.encode([query.lower()])[0]

        # Safe normalization for the query
        norm = np.linalg.norm(query_vector)
        norm = max(norm, 1e-9)
        query_normalized = query_vector / norm

        similarities = np.dot(self.utterance_matrix, query_normalized)
        best_match_idx = np.argmax(similarities)
        best_score = similarities[best_match_idx]
        logger.debug(best_score)

        if best_score >= self.threshold:
            return self.utterance_routes[best_match_idx]

        return None
