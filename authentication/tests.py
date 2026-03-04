from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status

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

