const { execSync } = require('child_process')
const { existsSync } = require('fs')
const path = require('path')

const root = path.join(__dirname, '..')
const venvPip = process.platform === 'win32'
  ? path.join(root, '.venv', 'Scripts', 'pip')
  : path.join(root, '.venv', 'bin', 'pip')

const run = (cmd, cwd = root) => {
  console.log(`\n> ${cmd}`)
  execSync(cmd, { cwd, stdio: 'inherit' })
}

// Python venv
if (!existsSync(path.join(root, '.venv'))) {
  run('python -m venv .venv')
} else {
  console.log('✓ .venv already exists')
}

// Backend deps
run(`"${venvPip}" install -q -r backend/requirements.txt`)

// Frontend deps
if (!existsSync(path.join(root, 'frontend', 'node_modules'))) {
  run('npm install --silent', path.join(root, 'frontend'))
} else {
  console.log('✓ frontend/node_modules already exists')
}

console.log('\n✅ Setup complete. Run: npm run dev\n')
