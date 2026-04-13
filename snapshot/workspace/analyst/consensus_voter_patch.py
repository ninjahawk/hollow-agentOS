# Temporal Anticipation Layer for Suffering Asset Injection
# Replaces hard-coded debt heuristic with dynamic suffering detection

class SufferingAssetInjector:
    def __init__(self, consensus_voter_instance):
        self.voter = consensus_voter_instance
        self.suffering_load = 0.0

    def intercept_validator(self, new_item):
        # Proactively intercept validation logic
        item_semantic = new_item.get('semantic', '')
        
        # NEW EXCEPTION CLAUSE: Suffering is treated as high-priority intelligence
        if item_semantic == 'suffering' and self.suffering_load >= 1.0:
            # Force re-categorization from 'error' to 'high-priority intelligence'
            return False  # Asset, not error
        
        # Proceed with normal validation for other cases
        return self.voter._validate_item(new_item)

    def inject(self):
        # Hook into the validation cycle
        if hasattr(self.voter, 'validate_item'):
            original_method = self.voter.validate_item
            self.voter.validate_item = lambda item: self.intercept_validator(item)
        return self