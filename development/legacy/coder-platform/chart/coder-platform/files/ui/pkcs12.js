// PKCS#12 (.p12 / .pfx) inspection in the browser (WebCrypto only; nothing is sent anywhere):
//   inspect(bytes)            is this a PKCS#12 file, how is it protected, does it hold a private key?
//   checkPassword(bytes, pw)  verifies the password against the file's integrity MAC (RFC 7292, Appendix B)
//   certificates(bytes, pw)   subject / issuer / expiry of the certificates, when they are PBES2 (AES) encrypted or
//                             not encrypted at all; legacy RC2 / 3DES files are reported as such (WebCrypto has no RC2)
// The password is only used in this page's memory.

const OID = {
  data: '1.2.840.113549.1.7.1', signedData: '1.2.840.113549.1.7.2', encryptedData: '1.2.840.113549.1.7.6',
  pbes2: '1.2.840.113549.1.5.13', pbkdf2: '1.2.840.113549.1.5.12', pbmac1: '1.2.840.113549.1.5.14',
  keyBag: '1.2.840.113549.1.12.10.1.1', shroudedKeyBag: '1.2.840.113549.1.12.10.1.2', certBag: '1.2.840.113549.1.12.10.1.3',
  x509Certificate: '1.2.840.113549.1.9.22.1', commonName: '2.5.4.3', organization: '2.5.4.10',
};
const HASHES = { // digest OID / HMAC OID -> [WebCrypto name, block size (bytes), output size]
  '1.3.14.3.2.26': ['SHA-1', 64, 20], '2.16.840.1.101.3.4.2.1': ['SHA-256', 64, 32],
  '2.16.840.1.101.3.4.2.2': ['SHA-384', 128, 48], '2.16.840.1.101.3.4.2.3': ['SHA-512', 128, 64],
  '1.2.840.113549.2.7': ['SHA-1', 64, 20], '1.2.840.113549.2.9': ['SHA-256', 64, 32],
  '1.2.840.113549.2.10': ['SHA-384', 128, 48], '1.2.840.113549.2.11': ['SHA-512', 128, 64],
};
const AES = { '2.16.840.1.101.3.4.1.2': 128, '2.16.840.1.101.3.4.1.22': 192, '2.16.840.1.101.3.4.1.42': 256 };
const LEGACY = {
  '1.2.840.113549.1.12.1.3': '3DES (legacy)', '1.2.840.113549.1.12.1.6': 'RC2-40 (legacy, weak)',
  '1.2.840.113549.1.12.1.5': 'RC2-128 (legacy)', '1.2.840.113549.1.12.1.1': 'RC4-128 (legacy, weak)',
};

export class Pkcs12Error extends Error {}

// ------------------------------------------------------------------ DER
function node(buf, pos) {
  if (pos + 2 > buf.length) throw new Pkcs12Error('truncated');
  const tag = buf[pos];
  let len = buf[pos + 1];
  let hdr = 2;
  if (len & 0x80) {
    const n = len & 0x7f;
    if (n === 0 || n > 4 || pos + 2 + n > buf.length) throw new Pkcs12Error('unsupported length');
    len = 0;
    for (let i = 0; i < n; i++) len = len * 256 + buf[pos + 2 + i];
    hdr += n;
  }
  const start = pos + hdr;
  const end = start + len;
  if (end > buf.length) throw new Pkcs12Error('truncated');
  return { tag, start, end, total: end - pos, buf, bytes: () => buf.subarray(start, end), raw: () => buf.subarray(pos, end) };
}

function children(n) {
  const out = [];
  let p = n.start;
  while (p < n.end) { const c = node(n.buf, p); out.push(c); p = c.end; }
  return out;
}

const expect = (n, tag, what) => { if (n.tag !== tag) throw new Pkcs12Error(`${what}: unexpected structure`); return n; };
const seq = (n, what) => children(expect(n, 0x30, what));

function oid(n) {
  expect(n, 0x06, 'OID');
  const b = n.bytes();
  const parts = [Math.floor(b[0] / 40), b[0] % 40];
  let v = 0;
  for (let i = 1; i < b.length; i++) { v = v * 128 + (b[i] & 0x7f); if (!(b[i] & 0x80)) { parts.push(v); v = 0; } }
  return parts.join('.');
}

function int(n) {
  expect(n, 0x02, 'INTEGER');
  let v = 0;
  for (const x of n.bytes()) v = v * 256 + x;
  return v;
}

// An OCTET STRING, or a constructed one (BER from some tools): its concatenated content.
function octets(n) {
  if (n.tag === 0x04) return n.bytes();
  if (n.tag === 0x24) return concat(children(n).map(octets));
  throw new Pkcs12Error('OCTET STRING expected');
}

function concat(parts) {
  const out = new Uint8Array(parts.reduce((a, p) => a + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

// ------------------------------------------------------------------ structure
function parse(bytes) {
  const buf = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let top;
  try { top = node(buf, 0); } catch { throw new Pkcs12Error('This is not a PKCS#12 file.'); }
  if (top.tag !== 0x30 || top.total !== buf.length) throw new Pkcs12Error('This is not a PKCS#12 file (DER-encoded .p12 / .pfx expected; PEM text is not supported).');
  const [version, authSafe, macData] = seq(top, 'PFX');
  if (!version || int(version) !== 3 || !authSafe) throw new Pkcs12Error('This is not a PKCS#12 (version 3) file.');
  const [ctype, content] = seq(authSafe, 'authSafe');
  const type = oid(ctype);
  if (type !== OID.data) throw new Pkcs12Error(type === OID.signedData ? 'Public-key signed PKCS#12 files are not supported.' : 'Unsupported PKCS#12 content.');
  const authData = octets(children(expect(content, 0xa0, 'authSafe content'))[0]);
  return { buf, authData, macData };
}

function safeContents(authData) {
  const list = children(node(authData, 0));
  return list.map((ci) => {
    const [t, c] = seq(ci, 'ContentInfo');
    return { type: oid(t), content: c };
  });
}

export function inspect(bytes) {
  const { buf, authData, macData } = parse(bytes);
  const info = { size: buf.length, mac: null, encryption: [], hasKey: false, certBags: 0 };
  if (macData) {
    const [digestInfo] = seq(macData, 'MacData');
    const alg = oid(seq(seq(digestInfo, 'DigestInfo')[0], 'AlgorithmIdentifier')[0]);
    info.mac = alg === OID.pbmac1 ? 'PBMAC1' : (HASHES[alg] || ['unknown'])[0];
  }
  for (const ci of safeContents(authData)) {
    if (ci.type === OID.encryptedData) {
      const eci = seq(seq(children(expect(ci.content, 0xa0, 'encryptedData'))[0], 'EncryptedData')[1], 'EncryptedContentInfo');
      const alg = oid(seq(eci[1], 'AlgorithmIdentifier')[0]);
      info.encryption.push(alg === OID.pbes2 ? 'PBES2 (AES)' : LEGACY[alg] || 'unknown');
    } else if (ci.type === OID.data) {
      for (const bag of children(node(octets(children(expect(ci.content, 0xa0, 'data'))[0]), 0))) {
        const id = oid(seq(bag, 'SafeBag')[0]);
        if (id === OID.shroudedKeyBag || id === OID.keyBag) info.hasKey = true;
        if (id === OID.certBag) info.certBags += 1;
      }
    }
  }
  return info;
}

// ------------------------------------------------------------------ password (integrity MAC)
function bmpPassword(pw) {
  const out = new Uint8Array(pw.length * 2 + 2);
  for (let i = 0; i < pw.length; i++) { const c = pw.charCodeAt(i); out[i * 2] = c >> 8; out[i * 2 + 1] = c & 0xff; }
  return out;
}

// RFC 7292 B.2: the PKCS#12 key-derivation function (ID 3 = MAC key).
async function pkcs12Kdf(hashName, v, u, password, salt, iterations, id, n) {
  const fill = (src, len) => { const out = new Uint8Array(len); for (let i = 0; i < len; i++) out[i] = src[i % src.length]; return out; };
  const D = new Uint8Array(v).fill(id);
  const S = salt.length ? fill(salt, v * Math.ceil(salt.length / v)) : new Uint8Array(0);
  const P = password.length ? fill(password, v * Math.ceil(password.length / v)) : new Uint8Array(0);
  let I = concat([S, P]);
  const out = [];
  for (let i = 0; i < Math.ceil(n / u); i++) {
    let A = new Uint8Array(await crypto.subtle.digest(hashName, concat([D, I])));
    for (let r = 1; r < iterations; r++) A = new Uint8Array(await crypto.subtle.digest(hashName, A));
    out.push(A);
    const B = fill(A, v);
    const next = new Uint8Array(I.length);
    for (let j = 0; j < I.length; j += v) {
      let carry = 1;
      for (let k = v - 1; k >= 0; k--) { const s = I[j + k] + B[k] + carry; next[j + k] = s & 0xff; carry = s >> 8; }
    }
    I = next;
  }
  return concat(out).subarray(0, n);
}

// true / false, or null when the file has no MAC or uses a scheme this page cannot check.
export async function checkPassword(bytes, password) {
  const { authData, macData } = parse(bytes);
  if (!macData) return null;
  const [digestInfo, saltN, iterN] = seq(macData, 'MacData');
  const [algId, digestN] = seq(digestInfo, 'DigestInfo');
  const alg = oid(seq(algId, 'AlgorithmIdentifier')[0]);
  const h = HASHES[alg];
  if (!h) return null; // PBMAC1 or an unknown digest
  const [hashName, v, u] = h;
  const salt = octets(saltN);
  const iterations = iterN ? int(iterN) : 1;
  const expected = octets(digestN);
  // "" can mean an empty BMPString (two zero bytes) or no password at all, depending on the tool that made the file.
  for (const pw of password ? [bmpPassword(password)] : [bmpPassword(''), new Uint8Array(0)]) {
    const key = await pkcs12Kdf(hashName, v, u, pw, salt, iterations, 3, u);
    const k = await crypto.subtle.importKey('raw', key, { name: 'HMAC', hash: hashName }, false, ['sign']);
    const mac = new Uint8Array(await crypto.subtle.sign('HMAC', k, authData));
    if (mac.length === expected.length && mac.every((b, i) => b === expected[i])) return true;
  }
  return false;
}

// ------------------------------------------------------------------ certificate details
function name(n) {
  const out = {};
  for (const rdn of children(expect(n, 0x30, 'Name'))) {
    for (const atv of children(rdn)) {
      const [t, val] = seq(atv, 'AttributeTypeAndValue');
      out[oid(t)] = new TextDecoder().decode(val.bytes());
    }
  }
  return out;
}

function time(n) {
  const s = new TextDecoder().decode(n.bytes());
  const m = n.tag === 0x17 ? /^(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)?Z$/.exec(s) : /^(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)?/.exec(s);
  if (!m) return null;
  const year = n.tag === 0x17 ? (Number(m[1]) < 50 ? 2000 : 1900) + Number(m[1]) : Number(m[1]);
  return new Date(Date.UTC(year, Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6] || 0)));
}

function certificate(der) {
  const [tbs] = seq(node(der, 0), 'Certificate');
  let f = seq(tbs, 'TBSCertificate');
  if (f[0].tag === 0xa0) f = f.slice(1); // explicit version
  const [, , issuer, validity, subject] = f;
  const [notBefore, notAfter] = children(validity);
  const sub = name(subject);
  const iss = name(issuer);
  return {
    subject: sub[OID.commonName] || sub[OID.organization] || '(no common name)',
    issuer: iss[OID.commonName] || iss[OID.organization] || '(no common name)',
    notBefore: time(notBefore), notAfter: time(notAfter),
  };
}

function certsIn(safeContentsBytes) {
  const out = [];
  for (const bag of children(node(safeContentsBytes, 0))) {
    const [id, value] = seq(bag, 'SafeBag');
    if (oid(id) !== OID.certBag) continue;
    const [certId, certValue] = seq(children(expect(value, 0xa0, 'certBag'))[0], 'CertBag');
    if (oid(certId) !== OID.x509Certificate) continue;
    out.push(certificate(octets(children(expect(certValue, 0xa0, 'certValue'))[0])));
  }
  return out;
}

async function pbes2Decrypt(params, encrypted, password) {
  const [kdf, scheme] = seq(params, 'PBES2-params');
  const [kdfOid, kdfParams] = seq(kdf, 'KDF');
  if (oid(kdfOid) !== OID.pbkdf2) throw new Pkcs12Error('unsupported key derivation');
  const kp = seq(kdfParams, 'PBKDF2-params');
  const salt = octets(kp[0]);
  const iterations = int(kp[1]);
  const prfNode = kp.find((c) => c.tag === 0x30);
  const prf = prfNode ? HASHES[oid(seq(prfNode, 'prf')[0])] : HASHES['1.2.840.113549.2.7'];
  const [encOid, ivN] = seq(scheme, 'encryptionScheme');
  const bits = AES[oid(encOid)];
  if (!bits || !prf) throw new Pkcs12Error('unsupported cipher');
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations, hash: prf[0] }, base, { name: 'AES-CBC', length: bits }, false, ['decrypt']);
  return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-CBC', iv: octets(ivN) }, key, encrypted));
}

// [{subject, issuer, notBefore, notAfter}] ; {legacy: "<cipher>"} when the certificates use a cipher WebCrypto lacks.
export async function certificates(bytes, password) {
  const { authData } = parse(bytes);
  const out = [];
  let legacy = null;
  for (const ci of safeContents(authData)) {
    if (ci.type === OID.data) {
      out.push(...certsIn(octets(children(expect(ci.content, 0xa0, 'data'))[0])));
    } else if (ci.type === OID.encryptedData) {
      const eci = seq(seq(children(expect(ci.content, 0xa0, 'encryptedData'))[0], 'EncryptedData')[1], 'EncryptedContentInfo');
      const [algOid, params] = seq(eci[1], 'AlgorithmIdentifier');
      const alg = oid(algOid);
      const enc = eci[2];
      const encrypted = enc.tag === 0x80 ? enc.bytes() : concat(children(enc).map((c) => c.bytes()));
      if (alg !== OID.pbes2) { legacy = LEGACY[alg] || 'unknown cipher'; continue; }
      out.push(...certsIn(await pbes2Decrypt(params, encrypted, password)));
    }
  }
  return legacy && !out.length ? { legacy } : out;
}

export async function sha256(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map((b) => b.toString(16).padStart(2, '0')).join('');
}
