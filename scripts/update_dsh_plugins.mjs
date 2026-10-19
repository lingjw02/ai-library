/**
 * DSH Plugin Automated Update & Smoke Test Script
 * Supports:
 * - pnpm outdated check & classification
 * - Local linked plugin verification (runs test suites)
 * - Safe backup & automated rollback
 * - Diagnostic reporting
 */

import { execSync, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync, copyFileSync, mkdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { homedir } from 'node:os';

const DSH_PROFILE_DIR = process.env.DSH_PROFILE_DIR || resolve(homedir(), '.dsh/profiles/web');
const TARGET_DSH_VERSION = '0.2.1-rc.2';

export function runCommand(cmd, cwd = DSH_PROFILE_DIR) {
  try {
    const stdout = execSync(cmd, { cwd, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'] });
    return { ok: true, stdout: stdout.trim(), stderr: '' };
  } catch (err) {
    return {
      ok: false,
      stdout: err.stdout ? String(err.stdout).trim() : '',
      stderr: err.stderr ? String(err.stderr).trim() : String(err.message),
      status: err.status,
    };
  }
}

export function checkOutdated() {
  const res = runCommand('pnpm outdated --json');
  // pnpm returns status 1 when outdated/deprecated packages exist
  const output = res.stdout || res.stderr;
  try {
    return JSON.parse(output);
  } catch {
    return {};
  }
}

export function verifyLocalPlugins(packageJson) {
  const localResults = [];
  const deps = packageJson.dependencies || {};
  for (const [name, versionSpec] of Object.entries(deps)) {
    if (typeof versionSpec === 'string' && versionSpec.startsWith('link:')) {
      const localPath = versionSpec.replace(/^link:/, '');
      const testDir = resolve(DSH_PROFILE_DIR, localPath, 'tests');
      let status = 'NO_TESTS';
      let details = 'No test suite found';

      if (existsSync(testDir)) {
        const testRun = spawnSync('node', ['--test', 'tests/*.test.mjs'], {
          cwd: resolve(DSH_PROFILE_DIR, localPath),
          encoding: 'utf-8',
          shell: true,
        });
        if (testRun.status === 0) {
          status = 'PASSED';
          details = 'All tests passed';
        } else {
          status = 'FAILED';
          details = testRun.stderr || testRun.stdout || 'Tests failed';
        }
      }

      localResults.push({ name, path: localPath, status, details });
    }
  }
  return localResults;
}

export function backupProfile(backupDir) {
  mkdirSync(backupDir, { recursive: true });
  const pkgSrc = join(DSH_PROFILE_DIR, 'package.json');
  const lockSrc = join(DSH_PROFILE_DIR, 'pnpm-lock.yaml');
  if (existsSync(pkgSrc)) copyFileSync(pkgSrc, join(backupDir, 'package.json'));
  if (existsSync(lockSrc)) copyFileSync(lockSrc, join(backupDir, 'pnpm-lock.yaml'));
}

export function rollbackProfile(backupDir) {
  const pkgBak = join(backupDir, 'package.json');
  const lockBak = join(backupDir, 'pnpm-lock.yaml');
  if (existsSync(pkgBak)) copyFileSync(pkgBak, join(DSH_PROFILE_DIR, 'package.json'));
  if (existsSync(lockBak)) copyFileSync(lockBak, join(DSH_PROFILE_DIR, 'pnpm-lock.yaml'));
  runCommand('pnpm install --frozen-lockfile');
}

export async function main() {
  const args = process.argv.slice(2);
  const isApply = args.includes('--apply');
  const isCheckOnly = args.includes('--check');

  console.log(`[DSH Plugin Manager] Profile: ${DSH_PROFILE_DIR}`);
  console.log(`[DSH Plugin Manager] Target DSH Runtime: ${TARGET_DSH_VERSION}`);

  const pkgPath = join(DSH_PROFILE_DIR, 'package.json');
  if (!existsSync(pkgPath)) {
    console.error(`Error: package.json not found in ${DSH_PROFILE_DIR}`);
    process.exit(1);
  }
  const packageJson = JSON.parse(readFileSync(pkgPath, 'utf-8'));

  // 1. Check local linked plugins
  console.log('\n--- 1. Local Linked Plugins Audit ---');
  const localPlugins = verifyLocalPlugins(packageJson);
  for (const lp of localPlugins) {
    const symbol = lp.status === 'PASSED' ? '✓' : lp.status === 'NO_TESTS' ? 'ℹ' : '✗';
    console.log(` ${symbol} ${lp.name} [${lp.status}]: ${lp.details}`);
  }

  // 2. Check outdated market plugins
  console.log('\n--- 2. Market Plugins Outdated Audit ---');
  const outdated = checkOutdated();
  const outdatedKeys = Object.keys(outdated);
  if (outdatedKeys.length === 0) {
    console.log(' All market packages are up to date.');
  } else {
    for (const [name, info] of Object.entries(outdated)) {
      const warn = info.isDeprecated ? ' [DEPRECATED]' : '';
      console.log(` - ${name}: current=${info.current}, wanted=${info.wanted}, latest=${info.latest}${warn}`);
    }
  }

  // 3. Dry-run summary or apply
  if (isCheckOnly || !isApply) {
    console.log('\n[Dry Run Completed] Use --apply to execute updates with automated backup & rollback.');
    return;
  }

  // If apply is specified:
  console.log('\n--- 3. Applying Updates ---');
  const backupDir = resolve(DSH_PROFILE_DIR, '.plugin-backup', String(Date.now()));
  console.log(`Backing up configuration to ${backupDir}...`);
  backupProfile(backupDir);

  try {
    console.log('Running pnpm update --interactive=false...');
    const updateRes = runCommand('pnpm update');
    if (!updateRes.ok) {
      throw new Error(`pnpm update failed: ${updateRes.stderr}`);
    }
    console.log('Updates applied successfully. Verifying local plugins...');
    const recheck = verifyLocalPlugins(packageJson);
    const hasFailures = recheck.some(p => p.status === 'FAILED');
    if (hasFailures) {
      throw new Error('Local plugin regression detected after update');
    }
    console.log('All smoke checks passed!');
  } catch (err) {
    console.error(`Update failed: ${err.message}. Initiating rollback...`);
    rollbackProfile(backupDir);
    console.log('Rollback completed.');
    process.exit(1);
  }
}

if (process.argv[1] && process.argv[1].endsWith('update_dsh_plugins.mjs')) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
