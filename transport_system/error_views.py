from django.contrib import messages
from django.shortcuts import redirect


def csrf_failure(request, reason="", *args, **kwargs):
    messages.error(
        request,
        "La session du formulaire a expire ou n'est plus valide. Recharge la page puis reessaie.",
    )
    target = request.META.get("HTTP_REFERER") or "/"
    return redirect(target)
