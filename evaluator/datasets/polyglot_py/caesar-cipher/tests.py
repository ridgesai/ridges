import unittest
import sys
import os

# Add the parent directory to the path so we can import the main module
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

# Import the functions from main.py
try:
    from main import encrypt, decrypt
    MODULE_IMPORTED = True
except ImportError:
    MODULE_IMPORTED = False

class TestCaesarCipher(unittest.TestCase):
    
    def setUp(self):
        if not MODULE_IMPORTED:
            self.skipTest("main.py module could not be imported")
    
    def test_encrypt_basic(self):
        self.assertEqual(encrypt("abc", 1), "bcd")
        
    def test_decrypt_basic(self):
        self.assertEqual(decrypt("bcd", 1), "abc")
        
    def test_encrypt_with_wrapping(self):
        self.assertEqual(encrypt("xyz", 3), "abc")
        
    def test_decrypt_with_wrapping(self):
        self.assertEqual(decrypt("abc", 3), "xyz")
        
    def test_encrypt_preserves_case(self):
        self.assertEqual(encrypt("Hello", 3), "Khoor")
        
    def test_decrypt_preserves_case(self):
        self.assertEqual(decrypt("Khoor", 3), "Hello")
        
    def test_encrypt_with_punctuation_and_spaces(self):
        self.assertEqual(encrypt("Hello, World!", 3), "Khoor, Zruog!")
        
    def test_decrypt_with_punctuation_and_spaces(self):
        self.assertEqual(decrypt("Khoor, Zruog!", 3), "Hello, World!")
        
    def test_encrypt_with_negative_shift(self):
        self.assertEqual(encrypt("def", -3), "abc")
        
    def test_decrypt_with_negative_shift(self):
        self.assertEqual(decrypt("abc", -3), "def")
        
    def test_encrypt_with_large_shift(self):
        # Test shift larger than alphabet size
        self.assertEqual(encrypt("abc", 29), "def")  # 29 % 26 = 3
        
    def test_decrypt_with_large_shift(self):
        # Test shift larger than alphabet size
        self.assertEqual(decrypt("def", 29), "abc")  # 29 % 26 = 3
        
    def test_encrypt_empty_string(self):
        self.assertEqual(encrypt("", 5), "")
        
    def test_decrypt_empty_string(self):
        self.assertEqual(decrypt("", 5), "")
        
    def test_encrypt_numbers_and_symbols_unchanged(self):
        self.assertEqual(encrypt("123 !@#", 5), "123 !@#")
        
    def test_decrypt_numbers_and_symbols_unchanged(self):
        self.assertEqual(decrypt("123 !@#", 5), "123 !@#")

if __name__ == "__main__":
    unittest.main()
