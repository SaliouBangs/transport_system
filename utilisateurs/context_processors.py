from .permissions import build_user_permissions


def user_access(request):
    return build_user_permissions(request.user)
