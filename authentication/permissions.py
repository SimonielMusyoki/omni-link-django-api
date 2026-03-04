from rest_framework import permissions
from authentication.models import UserRole


class IsAdmin(permissions.BasePermission):
    """Permission check for admin users"""

    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            request.user.is_admin()
        )


class IsManager(permissions.BasePermission):
    """Permission check for manager users and above"""

    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            request.user.is_manager()
        )


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
        return (
            request.user and
            request.user.is_authenticated and
            request.user.is_active
        )


class RoleBasedPermission(permissions.BasePermission):
    """
    Custom permission to check user roles.
    The view should have a 'required_roles' attribute with a list of allowed roles.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Get required roles from view
        required_roles = getattr(view, 'required_roles', None)

        if required_roles is None:
            # If no roles specified, allow authenticated users
            return True

        # Check if user's role is in required roles
        return request.user.role in required_roles or request.user.is_superuser

