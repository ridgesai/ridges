def encrypt(plaintext, shift):
    result = ""
    for char in plaintext:
        if char.isalpha():
            # Determine the ASCII offset (97 for lowercase, 65 for uppercase)
            ascii_offset = 65 if char.isupper() else 97
            # Shift the character and wrap around the alphabet
            shifted = (ord(char) - ascii_offset + shift) % 26
            result += chr(shifted + ascii_offset)
        else:
            # Non-alphabetic characters remain unchanged
            result += char
    return result

def decrypt(ciphertext, shift):
    # Decryption is just encryption with the negative shift
    return encrypt(ciphertext, -shift)
