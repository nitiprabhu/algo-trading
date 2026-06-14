from services.chartedge_core.database import batch_update_parameters
batch_update_parameters([
    ("risk", "options_max_loss_pct", 15.0, None),
    ("risk", "options_trail_arm_pct", 15.0, None)
])
print("Updated DB")
