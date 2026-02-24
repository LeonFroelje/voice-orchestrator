import logging

logger = logging.getLogger("STTSanitizer")


class NgramSanitizer:
    def __init__(self, threshold: float = 0.75):
        self.threshold = threshold
        self.known_vocabulary = set()

    def update_vocabulary(self, words: list[str]):
        """Dynamically update the known hot-words (e.g., from HA entities/areas)."""
        for word in words:
            # Only add meaningful words, ignore tiny words
            if len(word) > 3:
                self.known_vocabulary.add(word.lower())

    def _get_trigrams(self, text: str) -> set:
        """Converts a string into a set of character trigrams, ignoring spaces."""
        cleaned = text.lower().replace(" ", "")
        return set([cleaned[i : i + 3] for i in range(len(cleaned) - 2)])

    def _dice_coefficient(self, str1: str, str2: str) -> float:
        """Calculates the Sørensen–Dice coefficient between two strings."""
        tri1 = self._get_trigrams(str1)
        tri2 = self._get_trigrams(str2)

        if not tri1 or not tri2:
            return 0.0

        intersection = len(tri1.intersection(tri2))
        return (2.0 * intersection) / (len(tri1) + len(tri2))

    def sanitize(self, text: str) -> str:
        """Scans the STT text and replaces phonetic mistakes with known vocabulary."""
        sanitized_text = text.lower()
        words = sanitized_text.split()

        # We use a simple sliding window of 1 to 3 words to catch boundary errors
        # (e.g., checking "schreib", then "schreib die", then "schreib die schlampe")
        for window_size in range(3, 0, -1):
            for i in range(len(words) - window_size + 1):
                phrase_chunk = " ".join(words[i : i + window_size])

                # Compare this chunk against our known HA vocabulary
                best_match = None
                highest_score = 0.0

                for vocab_word in self.known_vocabulary:
                    score = self._dice_coefficient(phrase_chunk, vocab_word)
                    if score > highest_score:
                        highest_score = score
                        best_match = vocab_word

                # If we find a strong phonetic match, replace the chunk in the text
                if highest_score >= self.threshold and best_match:
                    logger.info(
                        f"STT Correction: '{phrase_chunk}' -> '{best_match}' (Score: {highest_score:.2f})"
                    )
                    sanitized_text = sanitized_text.replace(phrase_chunk, best_match)

        return sanitized_text
