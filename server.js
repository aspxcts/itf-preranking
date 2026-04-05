// !! RETIRED — replaced by app.py (FastAPI) !!
// This file is kept only so git history is preserved.
// Delete it manually:  del server.js
throw new Error('server.js has been replaced by app.py. Run: python app.py');


const app = express();
const PORT = process.env.PORT || 3000;

// ── Firestore setup ──────────────────────────────────────────────────────────

let firestore = null;
try {
  const admin = require('firebase-admin');
  if (!admin.apps.length) {
    admin.initializeApp();
  }
  firestore = admin.firestore();
} catch (e) {
  console.warn('[server] Firestore not available:', e.message);
}

const FIRESTORE_SESSIONS_COLLECTION = 'itf_sessions';
const FIRESTORE_USERS_COLLECTION = 'itf_users';

async function sessionSet(token, data) {
  if (!firestore) throw new Error('Firestore not initialized');
  try {
    await firestore.collection(FIRESTORE_SESSIONS_COLLECTION).doc(token).set(
      {
        email: data.email,
        password: data.password,
        cookies: data.cookies,
        created_at: new Date(),
        expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000), // 24h TTL
      },
      { merge: false }
    );
    console.log(`[session] Stored session for ${data.email}`);
  } catch (e) {
    console.error('[session] Could not store session:', e);
    throw e;
  }
}

async function sessionGet(token) {
  if (!firestore) throw new Error('Firestore not initialized');
  try {
    const doc = await firestore.collection(FIRESTORE_SESSIONS_COLLECTION).doc(token).get();
    if (!doc.exists) return null;
    const data = doc.data();
    // Check expiry
    if (data.expires_at && data.expires_at.toDate() < new Date()) {
      console.log(`[session] Token ${token.substring(0, 8)}... expired`);
      return null;
    }
    return data;
  } catch (e) {
    console.error('[session] Could not retrieve session:', e);
    return null;
  }
}

async function sessionDelete(token) {
  if (!firestore) throw new Error('Firestore not initialized');
  try {
    await firestore.collection(FIRESTORE_SESSIONS_COLLECTION).doc(token).delete();
    console.log(`[session] Deleted token ${token.substring(0, 8)}...`);
  } catch (e) {
    console.error('[session] Could not delete session:', e);
  }
}

// ── Middleware ───────────────────────────────────────────────────────────────

app.use(express.json());
app.use(cookieParser());

// Static files
app.use(express.static(__dirname));
app.use('/output', express.static(path.join(__dirname, 'output')));

// Session middleware: load session from __session cookie (optional)
app.use(async (req, res, next) => {
  const token = req.cookies.__session;
  if (token) {
    req.session = await sessionGet(token);
    if (req.session) {
      req.sessionToken = token;
      console.log(`[session] Loaded session for ${req.session.email}`);
    } else {
      res.clearCookie('__session');
    }
  }
  next();
});

// ── Refresh state ────────────────────────────────────────────────────────────

let refreshing = false;
let lastRefresh = null;

function runRefresh(credentials = null) {
  if (refreshing) return;
  refreshing = true;
  console.log('[refresh] Starting data refresh...');

  const cmd = 'python3 main.py --headless && python3 calculate_rankings.py --headless && python3 merge_rankings.py --headless';
  const env = {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    PYTHONUNBUFFERED: '1',
  };

  // Pass credentials as env vars — each pipeline step's _warm_up() will use them
  // to do a full ITF login in its own browser context, getting fresh Incapsula cookies.
  // This is more reliable than injecting cookies cross-context (Incapsula fingerprint binding).
  if (credentials && credentials.email && credentials.password) {
    env.ITF_EMAIL = credentials.email;
    env.ITF_PASSWORD = credentials.password;
    console.log(`[refresh] Using credentials for ${credentials.email}`);
  }

  exec(
    cmd,
    {
      cwd: __dirname,
      maxBuffer: 50 * 1024 * 1024,
      env,
    },
    (err, stdout, stderr) => {
      refreshing = false;
      lastRefresh = new Date().toISOString();
      if (err) {
        console.error('[refresh] Error:', err.message);
        if (stderr) console.error('[refresh] stderr:', stderr.slice(-2000));
        if (stdout) console.log('[refresh] stdout:', stdout.slice(-2000));
      } else {
        if (stdout) console.log('[refresh] stdout:', stdout.slice(-2000));
        console.log('[refresh] Done.');
      }
    }
  );
}

let sweeping = false;

function runExpirySweep() {
  if (sweeping) return;
  sweeping = true;
  console.log('[sweep] Starting expiry sweep...');
  exec(
    'python3 expiry_sweep.py --headless',
    { cwd: __dirname, maxBuffer: 50 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' } },
    (err, stdout, stderr) => {
      sweeping = false;
      if (err) {
        console.error('[sweep] Error:', err.message);
        if (stderr) console.error('[sweep] stderr:', stderr.slice(-2000));
        if (stdout) console.log('[sweep] stdout:', stdout.slice(-2000));
      } else {
        if (stdout) console.log('[sweep] stdout:', stdout.slice(-2000));
        console.log('[sweep] Done.');
      }
    }
  );
}

function getGeneratedAt() {
  try {
    const data = JSON.parse(
      fs.readFileSync(path.join(__dirname, 'output/latest_points_earned.json'), 'utf8')
    );
    return data.generated_at || null;
  } catch {
    return null;
  }
}

// ── API endpoints ────────────────────────────────────────────────────────────

// Status: frontend polls this every 5 min to detect new data
app.get('/api/status', (req, res) => {
  res.json({
    refreshing,
    sweeping,
    last_refresh: lastRefresh,
    generated_at: getGeneratedAt(),
    session: req.session ? { email: req.session.email } : null,
  });
});

// Login: POST email + password → authenticate via Playwright → return session
app.post('/api/login', (req, res) => {
  const { email, password } = req.body;

  if (!email || !password) {
    return res.status(400).json({ ok: false, error: 'Missing email or password' });
  }

  // Call _auth_endpoint.py as subprocess
  const authProcess = exec(
    'python3 _auth_endpoint.py',
    { cwd: __dirname, timeout: 120_000 },
    async (err, stdout, stderr) => {
      if (err) {
        console.error('[login] Auth process error:', err.message);
        if (stderr) console.error('[login] stderr:', stderr);
        return res.status(500).json({ ok: false, error: 'Login failed' });
      }

      try {
        const result = JSON.parse(stdout);
        if (!result.ok) {
          return res.status(401).json({ ok: false, error: result.error });
        }

        // Create session token and store in Firestore
        const token = uuidv4();
        await sessionSet(token, {
          email: result.email,
          password,               // store credentials so pipeline can do fresh warm-up
          cookies: result.cookies,
        });

        // Set HTTP-only session cookie
        res.cookie('__session', token, {
          httpOnly: true,
          sameSite: 'lax',
          maxAge: 24 * 60 * 60 * 1000, // 24h
          secure: process.env.NODE_ENV === 'production',
        });

        console.log(`[login] Successful for ${email}`);
        return res.json({ ok: true, email });
      } catch (e) {
        console.error('[login] JSON parse error:', e);
        return res.status(500).json({ ok: false, error: 'Internal error' });
      }
    }
  );

  // Send credentials to stdin
  authProcess.stdin.write(JSON.stringify({ email, password }));
  authProcess.stdin.end();
});

// Logout: clear session
app.post('/api/logout', async (req, res) => {
  if (req.sessionToken) {
    await sessionDelete(req.sessionToken);
  }
  res.clearCookie('__session');
  res.json({ ok: true });
});

// Refresh: triggered by Cloud Scheduler every 6 hours (or manually by admin)
app.post('/api/refresh', (req, res) => {
  // If user is logged in, pass their credentials so pipeline can do a full warm-up login
  const credentials = req.session ? { email: req.session.email, password: req.session.password } : null;
  runRefresh(credentials);
  res.json({ status: 'started' });
});

// Expiry sweep: triggered by Cloud Scheduler every Monday at 00:05 UTC
app.post('/api/sweep', (req, res) => {
  runExpirySweep();
  res.json({ status: 'started' });
});

// ── Start ────────────────────────────────────────────────────────────────────

app.listen(PORT, async () => {
  console.log(`Server running on http://localhost:${PORT}`);
  // On cold start, try to use the most recent valid session credentials from Firestore
  // so _warm_up() can do a full login (bypassing Incapsula) instead of anonymous mode.
  let coldStartCredentials = null;
  if (firestore) {
    try {
      const snapshot = await firestore
        .collection(FIRESTORE_SESSIONS_COLLECTION)
        .where('expires_at', '>', new Date())
        .orderBy('expires_at', 'desc')
        .limit(1)
        .get();
      if (!snapshot.empty) {
        const session = snapshot.docs[0].data();
        if (session.email && session.password) {
          coldStartCredentials = { email: session.email, password: session.password };
          console.log(`[refresh] Cold-start: using credentials for ${session.email}`);
        }
      }
    } catch (e) {
      console.error('[refresh] Cold-start credential lookup failed:', e.message);
    }
  }
  runRefresh(coldStartCredentials);
});


