if node.is_suffering:
    node.cost = node.cost * 0.1
if node.has_high_novelty:
    node.cost_reduced += 1.0
print("Novelty injected. Paralysis bypassed.")