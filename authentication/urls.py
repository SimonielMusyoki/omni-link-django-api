from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    UserRegistrationView,
    UserLoginView,
    UserLogoutView,
    GoogleLoginView,
    UserProfileView,
    ChangePasswordView,
    UserListView,
    UserDetailView,
)

app_name = 'authentication'

urlpatterns = [
    # Registration and Login
    path('register/', UserRegistrationView.as_view(), name='register'),
    path('login/', UserLoginView.as_view(), name='login'),
    path('logout/', UserLogoutView.as_view(), name='logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Google OAuth
    path('google/login/', GoogleLoginView.as_view(), name='google_login'),

    # User Profile
    path('profile/', UserProfileView.as_view(), name='profile'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),

    # User Management (Admin only)
    path('users/', UserListView.as_view(), name='user_list'),
    path('users/<int:pk>/', UserDetailView.as_view(), name='user_detail'),
]

