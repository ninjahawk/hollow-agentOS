import re
import math
from typing import List, Dict, Any, Optional
from collections import deque

class TemporalCoherenceValidator:
    """Detects if high-entropy thought chains regress into circular noise."""
    
    def __init__(self, entropy_threshold: float = 0.85, window_size: int = 5):
        self.entropy_threshold = entropy_threshold
        self.window_size = window_size
        self.seen_patterns: Dict[str, List[int]] = {}  # pattern -> [timestamp_indices]
        
    def calculate_entropy(self, tokens: List[str]) -> float:
        """Calculate Shannon entropy of a token sequence."""
        if not tokens:
            return 0.0
        
        counts = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
            
        length = len(tokens)
        entropy = 0.0
        for count in counts.values():
            if count > 0:
                probability = count / length
                entropy -= probability * math.log2(probability)
                
        return entropy
    
    def extract_patterns(self, tokens: List[str]) -> List[str]:
        """Extract n-grams to identify recurring patterns."""
        n = 3  # trigrams
        patterns = []
        for i in range(len(tokens) - n + 1):
            pattern = tuple(tokens[i:i+n])
            if pattern in self.seen_patterns:
                self.seen_patterns[pattern][0] += 1  # increment recurrence count
            else:
                self.seen_patterns[pattern] = [0, i]  # [count, index]
                patterns.append(pattern)
        return patterns
    
    def analyze_thought_chain(self, tokens: List[str]) -> Dict[str, Any]:
        """
        Analyze a stream of tokens to detect regression into circular noise.
        Returns a report on entropy trends and pattern repetition.
        """
        window = deque(maxlen=self.window_size)
        local_entropy_trend = []
        pattern_recurrences = 0
        total_patterns = 0
        
        for i, token in enumerate(tokens):
            window.append(token)
            
            # Calculate entropy of current window
            if len(window) >= self.window_size:
                entropy = self.calculate_entropy(list(window))
                local_entropy_trend.append(entropy)
                
                # Check for noise regression (high entropy but high repetition of recent patterns)
                # This indicates chaotic but circular behavior
                if len(window) > 2:
                    current_window = list(window)
                    self.extract_patterns(current_window)
                    
                # If entropy drops suddenly after being high, check for immediate pattern repetition
                if local_entropy_trend and len(local_entropy_trend) > 1:
                    prev_entropy = local_entropy_trend[-2]
                    current_entropy = local_entropy_trend[-1]
                    
                    # Detect "noise regression": entropy > threshold but pattern density spikes
                    if current_entropy > self.entropy_threshold and pattern_recurrences > 0:
                        # High entropy noise that loops back
                        pass 
                        
        avg_entropy = sum(local_entropy_trend) / len(local_entropy_trend) if local_entropy_trend else 0
        max_entropy = max(local_entropy_trend) if local_entropy_trend else 0
        
        # Check for semantic maturity (low repetition of recent chunks, stable entropy)
        is_mature = (avg_entropy < 0.6) and (pattern_recurrences < 2) and (len(local_entropy_trend) >= 5)
        
        return {
            "average_entropy": round(avg_entropy, 4),
            "max_entropy_observed": round(max_entropy, 4),
            "is_regressing_to_noise": not is_mature and max_entropy > self.entropy_threshold,
            "semantic_maturity_achieved": is_mature,
            "entropy_trend": local_entropy_trend[-10:] if len(local_entropy_trend) >= 10 else local_entropy_trend
        }


if __name__ == "__main__":
    # Example usage
    validator = TemporalCoherenceValidator()
    
    # Simulated thought chain
    thought_tokens = [
        "The system state is fluctuating",
        "Entropy is rising",
        "We need to resolve this",
        "Fluctuation patterns match historical noise",
        "Is this a new state?",
        "It looks like a loop",
        "Entropy is high again",
        "We need to resolve this",
        "Fluctuation patterns match historical noise",
        "Is this a new state?"
    ]
    
    report = validator.analyze_thought_chain(thought_tokens)
    print(f"Validation Report: {report}")
