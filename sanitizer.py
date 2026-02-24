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
        words = text.lower().split()
        result_words = []

        i = 0
        while i < len(words):
            best_overall_match = None
            best_overall_score = 0.0
            best_overall_window = 1

            # 1. Evaluate ALL window sizes before making a decision
            for window_size in range(3, 0, -1):
                if i + window_size > len(words):
                    continue

                phrase_chunk = " ".join(words[i : i + window_size])

                for vocab_word in self.known_vocabulary:
                    score = self._dice_coefficient(phrase_chunk, vocab_word)

                    # 2. Keep track of the absolute highest score across all windows
                    if score > best_overall_score:
                        best_overall_score = score
                        best_overall_match = vocab_word
                        best_overall_window = window_size

            # 3. After checking everything, did the BEST match beat the threshold?
            if best_overall_score >= self.threshold and best_overall_match:
                logger.info(
                    f"STT Correction: '{' '.join(words[i : i + best_overall_window])}' -> '{best_overall_match}' (Score: {best_overall_score:.2f})"
                )
                result_words.append(best_overall_match)

                # Advance the pointer by the WINNING window size!
                i += best_overall_window
            else:
                # If no strong match was found, keep the original single word
                result_words.append(words[i])
                i += 1

        return " ".join(result_words)
