import base64
import random
import binascii
import hashlib
from Crypto.Cipher import AES
from Crypto import Random

BLOCK_SIZE = 16
pad = lambda s: s + (BLOCK_SIZE - len(s) % BLOCK_SIZE) * chr(BLOCK_SIZE - len(s) % BLOCK_SIZE)
unpad = lambda s: s[:-ord(s[len(s) - 1:])]

def int_to_hex(x):
    return format(x, 'x')

def int_to_bytes(x):
    return x.to_bytes((x.bit_length() + 7) // 8, 'big')

def bytes_to_int(x):
    return int.from_bytes(x, 'big')

def bytes_to_hex(x):
    return binascii.hexlify(x).decode('utf-8')

def hex_to_bytes(x):
    return binascii.unhexlify(x)

def hex_to_int(x):
    return int(x, 16)

def AesDecryptWithKey(encMessage, key):
    encMessage = base64.b64decode(encMessage)

    iv = encMessage[:BLOCK_SIZE]
    cipherText = encMessage[BLOCK_SIZE:len(encMessage)-BLOCK_SIZE]
    macTag = encMessage[len(encMessage)-BLOCK_SIZE:]

    cipher = AES.new(key, AES.MODE_GCM, iv)
    decrypted = cipher.decrypt_and_verify(cipherText, macTag)
    return decrypted

class CustomSRP():
    def SRPStage1(self):
        self.N = 4983313092069490398852700692508795473567251422586244806694940877242664573189903192937797446992068818099986958054998012331720869136296780936009508700487789962429161515853541556719593346959929531150706457338429058926505817847524855862259333438239756474464759974189984231409170758360686392625635632084395639143229889862041528635906990913087245817959460948345336333086784608823084788906689865566621015175424691535711520273786261989851360868669067101108956159530739641990220546209432953829448997561743719584980402874346226230488627145977608389858706391858138200618631385210304429902847702141587470513336905449351327122086464725143970313054358650488241167131544692349123381333204515637608656643608393788598011108539679620836313915590459891513992208387515629240292926570894321165482608544030173975452781623791805196546326996790536207359143527182077625412731080411108775183565594553871817639221414953634530830290393130518228654795859
        self.g = 2
        a = random.getrandbits(256)
        if a < 0:
            a += self.N
        self.a = a

        self.A = pow(self.g, self.a, self.N)
        return int_to_hex(self.A)
    
    def computeLittleS(self):
        N_bytes = int_to_bytes(self.N)
        g_bytes = int_to_bytes(self.g)
        
        # Prepend g_bytes with |N_bytes|-|g_bytes| of 0
        g_bytes = (b"\0" * (len(N_bytes) - len(g_bytes))) + g_bytes

        m = hashlib.sha256()
        m.update(str(self.N).encode('utf-8') + g_bytes)
        digest = m.hexdigest()

        return hex_to_int(digest)

    def computeU(self):
        N_length = len(int_to_bytes(self.N))
        A_bytes = int_to_bytes(self.A)
        B_bytes = int_to_bytes(self.B)

        # Prepend A_bytes with |N_bytes|-|A_bytes| of 0
        A_bytes = (b"\0"*(N_length-len(A_bytes)))+A_bytes

        # Prepend r_bytes with |N_bytes|-|r_bytes| of 0
        B_bytes = (b"\0"*(N_length-len(B_bytes)))+B_bytes
        
        m = hashlib.sha256()
        m.update(A_bytes + B_bytes)
        hashed = m.hexdigest()
        u = hex_to_int(hashed) % self.N
        return u

    def computeSessionKey(self):
        u = self.computeU()
        # The MitID app does not seem to perform this safety check
        # but at least some other implementations do 
        #if u == 0:
        #    return None

        s = self.computeLittleS()
        
        a = u * self.hashed_password + self.a
        c = pow((self.B - (pow(self.g, self.hashed_password, self.N) * s)), a, self.N)
        if a < 0:
            a += self.N

        return c

    def computeM1(self, r, srpSalt):
        m = hashlib.sha256()
        m.update(str(self.N).encode("utf-8"))
        N = hex_to_int(m.hexdigest())

        m = hashlib.sha256()
        m.update(str(self.g).encode("utf-8"))
        g = hex_to_int(m.hexdigest())
        a = N ^ g

        m = hashlib.sha256()
        m.update((str(a) + r + srpSalt + str(self.A) + str(self.B) + bytes_to_hex(self.K_bits)).encode("ascii"))
        return m.hexdigest()

    def SRPStage3(self, srpSalt, randomB, password, auth_session_id):
        self.B = hex_to_int(randomB)

        if(self.B == 0 or self.B % self.N == 0):
            raise Exception("randomB did not pass safety check")

        m = hashlib.sha256()
        m.update((srpSalt + password).encode("ascii"))
        self.hashed_password = hex_to_int(m.hexdigest())

        a = self.computeSessionKey()

        m = hashlib.sha256()
        m.update(str(a).encode("utf-8"))
        self.K_bits = m.digest()

        m = hashlib.sha256()
        m.update(auth_session_id.encode("utf-8"))
        I_hex = m.hexdigest()

        self.M1_hex = self.computeM1(I_hex, srpSalt)

        return self.M1_hex
    
    # Should satisfy if the server is correct
    # Interestingly enough, this cannot be checked for the pin-binding proof
    def SRPStage5(self, M2_hex):
        M1_bigInt = int(self.M1_hex, 16)

        m = hashlib.sha256()
        m.update((str(self.A) + str(M1_bigInt) + bytes_to_hex(self.K_bits)).encode('utf-8'))
        M2_hex_verify = m.hexdigest()
        return M2_hex_verify == M2_hex

    def AuthEnc(self, plainText):
        iv = Random.new().read(BLOCK_SIZE)
        cipher = AES.new(self.K_bits, AES.MODE_GCM, iv)
        ciphertext, tag = cipher.encrypt_and_digest(plainText)
        return (iv + ciphertext + tag)

    def AuthDec(self, encMessage):
        return AesDecryptWithKey(encMessage, self.K_bits)
    
    def AuthDecPin(self, encMessage):
        pin_key = hashlib.sha256(binascii.hexlify(self.K_bits) + b"PIN").digest()
        return AesDecryptWithKey(encMessage, pin_key)
