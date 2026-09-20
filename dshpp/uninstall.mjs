#!/usr/bin/env node
/**
 * DSH++ embedded uninstaller — removes dsh-plugin-dshpp from DSH Desktop.
 *
 *   node uninstall.mjs [--profile <dir>] [--skin-root <dir>]
 *
 * Reverses install.mjs exactly: removes the managed block from the profile
 * cordis.patch.yml, deletes the bundle copy in the DSH++ plugin tree, and
 * drops the A-track registry entry. Idempotent: each step is a no-op when
 * already absent, so this is also the emergency path if the GUI is broken
 * (it only touches files DSH++ itself wrote — never pnpm state, market
 * state, dshskin blocks, or other profiles).
 *
 * A running desktop may drop the row via live patch reload; a restart
 * guarantees the next boot is clean. This script never starts or restarts
 * DSH Desktop.
 */

import fs from 'node:fs';
import path from 'node:path';
import {
  resolveSkinRoot,
  resolveProfileDir,
  snapshotFiles,
  uninstallBlock,
  removeBundle,
  removeRegistry,
} from './installer-core.mjs';

function argValue(flag, dflt) {
  const argv = process.argv.slice(2);
  const i = argv.indexOf(flag);
  if (i !== -1 && i + 1 < argv.length) return argv[i + 1];
  return dflt;
}

function main() {
  const explicitProfile = argValue('--profile', null);
  const explicitSkinRoot = argValue('--skin-root', null);

  const resolvedSkin = resolveSkinRoot();
  const resolvedProfile = resolveProfileDir(explicitSkinRoot ? path.resolve(explicitSkinRoot) : resolvedSkin.dir);

  const skinRoot = path.resolve(explicitSkinRoot ?? resolvedSkin.dir);
  const profileDir = (() => {
    if (explicitProfile) return path.resolve(explicitProfile);
    if (resolvedProfile.dir) return resolvedProfile.dir;
    return null; // dshHome 解析失败：patch 步骤跳过，bundle/registry 仍按 skinRoot 清理
  })();

  console.log('DSH++ uninstaller (dshskin file:// pattern)');
  console.log(`  skin root : ${skinRoot}   (${explicitSkinRoot ? '显式 --skin-root' : resolvedSkin.source})`);
  console.log(`  profile   : ${profileDir ?? '(unresolved — patch 步骤将跳过)'}`);
  if (profileDir && !fs.existsSync(path.join(profileDir, 'cordis.patch.yml'))) {
    console.log('  (profile 下无 cordis.patch.yml，patch 步骤将按缺省处理)');
  }

  const snap = snapshotFiles('uninstall', profileDir, skinRoot);
  console.log(`  snapshot  : ${snap.dir}  [${snap.copied.join(', ')}]`);

  let block = null;
  if (profileDir) {
    block = uninstallBlock(profileDir);
    console.log(`  patch     : ${block.action}`);
    console.log(`              ${block.file}`);
  } else {
    console.log('  patch     : skipped（profile 未解析，bundle/registry 继续清理）');
  }

  const bundle = removeBundle(skinRoot);
  console.log(`  bundle    : ${bundle.removed ? `removed ${bundle.dir}` : 'bundle copy absent — nothing to do'}`);

  const reg = removeRegistry(skinRoot);
  console.log(`  registry  : ${reg.action}`);

  console.log('');
  console.log('Uninstalled. Restart DSH Desktop to guarantee the next boot is clean.');
  console.log('This script never starts or restarts DSH Desktop — restart it yourself.');
}

try {
  main();
} catch (e) {
  console.error(`\nuninstall FAILED: ${e.message}`);
  process.exit(1);
}
