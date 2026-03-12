from rest_framework import permissions


def _authenticated_user(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None
    return user


class IsAdmin(permissions.BasePermission):
    """Permission check for admin or owner users."""

    def has_permission(self, request, view):
        user = _authenticated_user(request)
        return bool(user and user.is_admin())


class IsAdminOrOwner(IsAdmin):
    """Explicit alias for admin/owner-only endpoints."""


class IsManager(permissions.BasePermission):
    """Permission check for manager users and above."""

    def has_permission(self, request, view):
        user = _authenticated_user(request)
        return bool(user and user.is_manager())


class IsManagerOrAbove(IsManager):
    """Explicit alias for manager/admin/owner endpoints."""


class IsOwnerOrAdmin(permissions.BasePermission):
    """Permission check for object owner or admin"""

    def has_object_permission(self, request, view, obj):
        # Admin can access anything
        if request.user.is_admin():
            return True

        # Check if object has user attribute
        if hasattr(obj, 'user'):
            return obj.user == request.user

        # Check if object is the user itself
        return obj == request.user


class IsActiveUser(permissions.BasePermission):
    """Permission check for active users"""

    def has_permission(self, request, view):
        user = _authenticated_user(request)
        return bool(user and user.is_active)


class RoleBasedPermission(permissions.BasePermission):
    """
    Custom permission to check user roles.
    The view should have a 'required_roles' attribute with a list of allowed roles.
    """

    def has_permission(self, request, view):
        user = _authenticated_user(request)
        if not user:
            return False

        if user.is_owner() or user.is_superuser:
            return True

        # Get required roles from view
        required_roles = getattr(view, 'required_roles', None)

        if required_roles is None:
            # If no roles specified, allow authenticated users
            return True

        # Check if user's role is in required roles
        return user.role in required_roles or user.is_superuser

