from rest_framework import status, generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import get_user_model
import requests

from .serializers import (
    UserRegistrationSerializer,
    UserSerializer,
    UserUpdateSerializer,
    UserSelfUpdateSerializer,
    ChangePasswordSerializer,
    GoogleLoginSerializer
)
from .permissions import IsAdminOrOwner, IsOwnerOrAdmin

User = get_user_model()


class UserRegistrationView(generics.CreateAPIView):
    """View for user registration with email and password"""
    queryset = User.objects.all()
    permission_classes = [permissions.AllowAny]
    serializer_class = UserRegistrationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Generate JWT tokens
        refresh = RefreshToken.for_user(user)

        return Response({
            'user': UserSerializer(user).data,
            'tokens': {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            },
            'message': 'User registered successfully'
        }, status=status.HTTP_201_CREATED)


class UserLoginView(TokenObtainPairView):
    """View for user login with email and password"""
    pass


class UserLogoutView(APIView):
    """View for user logout (blacklist refresh token)"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get('refresh_token')
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
            return Response({
                'message': 'Logged out successfully'
            }, status=status.HTTP_205_RESET_CONTENT)
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


class GoogleLoginView(APIView):
    """View for Google OAuth login"""
    permission_classes = [permissions.AllowAny]
    serializer_class = GoogleLoginSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        access_token = serializer.validated_data['access_token']

        try:
            # Verify Google access token
            google_user_info = self.verify_google_token(access_token)

            if not google_user_info:
                return Response({
                    'error': 'Invalid Google access token'
                }, status=status.HTTP_401_UNAUTHORIZED)

            # Get or create user
            user, created = self.get_or_create_user(google_user_info)

            # Generate JWT tokens
            refresh = RefreshToken.for_user(user)

            return Response({
                'user': UserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                },
                'message': 'Logged in successfully with Google' if not created else 'Account created and logged in with Google'
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                'error': f'Google authentication failed: {str(e)}'
            }, status=status.HTTP_400_BAD_REQUEST)

    def verify_google_token(self, access_token):
        """Verify Google access token and get user info"""
        try:
            # Call Google's userinfo endpoint
            response = requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {access_token}'}
            )

            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Error verifying Google token: {e}")
            return None

    def get_or_create_user(self, google_user_info):
        """Get or create user from Google user info"""
        email = google_user_info.get('email')
        google_id = google_user_info.get('sub')

        # Try to find user by Google ID or email
        user = User.objects.filter(google_id=google_id).first()

        if not user:
            user = User.objects.filter(email=email).first()

        created = False
        if user:
            # Update Google ID if not set
            if not user.google_id:
                user.google_id = google_id
                user.save()
        else:
            # Create new user
            user = User.objects.create_user(
                email=email,
                google_id=google_id,
                first_name=google_user_info.get('given_name', ''),
                last_name=google_user_info.get('family_name', ''),
                profile_picture=google_user_info.get('picture', ''),
            )
            created = True

        return user, created


class UserProfileView(generics.RetrieveUpdateAPIView):
    """View for retrieving and updating user profile"""
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrAdmin]
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.request.method == 'PUT' or self.request.method == 'PATCH':
            return UserSelfUpdateSerializer
        return UserSerializer


class ChangePasswordView(APIView):
    """View for changing user password"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={'request': request}
        )

        if serializer.is_valid():
            serializer.save()
            return Response({
                'message': 'Password changed successfully'
            }, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserListView(generics.ListAPIView):
    """List users: any authenticated user can read; writes remain admin/owner-only."""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrOwner]

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [permission() for permission in self.permission_classes]


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """User detail: any authenticated user can read; writes remain admin/owner-only."""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrOwner]

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [permission() for permission in self.permission_classes]

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return UserUpdateSerializer
        return UserSerializer

