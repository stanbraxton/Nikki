"""Print a bcrypt hash for ADMIN_PASSWORD_HASH.  usage: python scripts/hash_password.py 'secret'"""
import sys, bcrypt
print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt(12)).decode())
