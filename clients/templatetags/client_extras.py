from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template
from django.utils.formats import number_format

register = template.Library()


@register.filter
def gnf(value):
    if value in (None, "", "-"):
        return "-"
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value

    amount = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{number_format(amount, decimal_pos=0, use_l10n=True, force_grouping=True)} GNF"
