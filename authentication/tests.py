from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from authentication.models import UserRole

User = get_user_model()


class AuthenticationTest(APITestCase):
    """Tests for authentication endpoints"""

    def setUp(self):
        self.client = APIClient()

    def test_user_registration(self):
        """Test user registration"""
        data = {
            'email': 'newuser@test.com',
            'password': 'TestPass123!',
            'password_confirm': 'TestPass123!',
            'first_name': 'Test',
            'last_name': 'User'
        }
        response = self.client.post('/api/auth/register/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('tokens', response.data)
        self.assertIn('access', response.data['tokens'])
        self.assertIn('refresh', response.data['tokens'])

    def test_user_login(self):
        """Test user login"""
        # Create user first
        User.objects.create_user(
            email='testuser@test.com',
            password='TestPass123!'
        )

        # Login
        data = {
            'email': 'testuser@test.com',
            'password': 'TestPass123!'
        }
        response = self.client.post('/api/auth/login/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_user_profile(self):
        """Test getting user profile"""
        user = User.objects.create_user(
            email='testuser@test.com',
            password='TestPass123!',
            first_name='Test',
            last_name='User'
        )

        self.client.force_authenticate(user=user)
        response = self.client.get('/api/auth/profile/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email'], 'testuser@test.com')
        self.assertEqual(response.data['first_name'], 'Test')

    def test_change_password(self):
        """Test changing password"""
        user = User.objects.create_user(
            email='testuser@test.com',
            password='OldPass123!'
        )

        self.client.force_authenticate(user=user)
        data = {
            'old_password': 'OldPass123!',
            'new_password': 'NewPass123!',
            'new_password_confirm': 'NewPass123!'
        }
        response = self.client.post('/api/auth/change-password/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify new password works
        user.refresh_from_db()
        self.assertTrue(user.check_password('NewPass123!'))

    def test_profile_update_cannot_change_own_role(self):
        user = User.objects.create_user(
            email='self@test.com',
            password='OldPass123!',
            role=UserRole.USER,
        )

        self.client.force_authenticate(user=user)
        response = self.client.patch(
            '/api/auth/profile/',
            {
                'first_name': 'Updated',
                'role': UserRole.ADMIN,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Updated')
        self.assertEqual(user.role, UserRole.USER)


class UserRoleModelTests(TestCase):
    def test_owner_role_has_full_access_helpers(self):
        owner = User.objects.create_user(
            email='owner@test.com',
            password='OwnerPass123!',
            role=UserRole.OWNER,
        )

        self.assertTrue(owner.is_owner())
        self.assertTrue(owner.is_admin())
        self.assertTrue(owner.is_manager())


class UserManagementPermissionTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email='owner@test.com',
            password='OwnerPass123!',
            role=UserRole.OWNER,
        )
        self.admin = User.objects.create_user(
            email='admin@test.com',
            password='AdminPass123!',
            role=UserRole.ADMIN,
        )
        self.manager = User.objects.create_user(
            email='manager@test.com',
            password='ManagerPass123!',
            role=UserRole.MANAGER,
        )
        self.user = User.objects.create_user(
            email='user@test.com',
            password='UserPass123!',
            role=UserRole.USER,
        )
        self.target = User.objects.create_user(
            email='target@test.com',
            password='TargetPass123!',
        )

    def test_owner_can_list_users(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_can_update_users(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f'/api/auth/users/{self.target.pk}/',
            {'role': UserRole.MANAGER},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertEqual(self.target.role, UserRole.MANAGER)

    def test_manager_can_list_users(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_user_can_list_users(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_user_can_retrieve_user_detail(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f'/api/auth/users/{self.target.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_cannot_list_users(self):
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_cannot_retrieve_user_detail(self):
        response = self.client.get(f'/api/auth/users/{self.target.pk}/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_manager_cannot_update_users(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.patch(
            f'/api/auth/users/{self.target.pk}/',
            {'role': UserRole.USER},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_cannot_update_users(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(
            f'/api/auth/users/{self.target.pk}/',
            {'role': UserRole.USER},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

