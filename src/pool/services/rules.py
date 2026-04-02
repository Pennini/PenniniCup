PHASE_GROUP = "GROUP"
PHASE_KNOCKOUT = "KNOCKOUT"


def normalize_stage_key(stage):
    if not stage:
        return ""

    stage_name = (stage.name or "").upper().replace("-", " ").strip()
    if "GROUP" in stage_name or "GRUPO" in stage_name or "PRIMEIRA FASE" in stage_name:
        return "GROUP"
    if "SEMI" in stage_name or "SF" in stage_name:
        return "SF"
    if "QUART" in stage_name or "QF" in stage_name:
        return "QF"
    if "R16" in stage_name or "OITAV" in stage_name or "ROUND OF 16" in stage_name:
        return "R16"
    if "R32" in stage_name or "32 AVOS" in stage_name or "SEGUNDAS DE FINAL" in stage_name:
        return "R32"
    if "DECIS" in stage_name and "3" in stage_name:
        return "THIRD"
    if "TERCE" in stage_name and "LUGAR" in stage_name:
        return "THIRD"
    if stage_name == "FINAL":
        return "FINAL"
    if "FINAL" in stage_name and "SEMI" not in stage_name and "QUART" not in stage_name and "OITAV" not in stage_name:
        return "FINAL"
    return ""


def phase_for_match(match):
    stage_key = normalize_stage_key(match.stage)
    if stage_key == "GROUP":
        return PHASE_GROUP
    return PHASE_KNOCKOUT
