from django.contrib import messages


def resolve_selected_participation(request, participations):
    """Resolve a participação selecionada via ?pool=<slug> ou o primeiro entrado.

    `participations` deve vir ordenado por joined_at (primeiro entrado primeiro).
    Retorna (participation_or_None, selected_slug).
    """
    selected_slug = (request.GET.get("pool") or "").strip()
    selected = None
    if selected_slug:
        selected = next((p for p in participations if p.pool.slug == selected_slug), None)
        if selected is None:
            messages.warning(
                request,
                "Bolão selecionado não encontrado entre suas participações ativas.",
            )
    if selected is None and participations:
        selected = participations[0]
    return selected, selected_slug
