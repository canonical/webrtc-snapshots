#!/usr/bin/python3
# Copyright 2014 Google Inc.
# Copyright 2024 Canonical Ltd. All rigths reserved.
#
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE.fetch-webrtc.py file.

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

WEBRTC_URL = 'https://webrtc.googlesource.com/src'

DEFAULT_DEPS_PATH = 'src/DEPS'
DEPS_TO_USE = [
  'src/build',
  'src/testing',
  'src/tools',
  'src/third_party',
  'src/third_party/libyuv',
  'src/third_party/nasm',
  'src/third_party/libsrtp',
  'src/third_party/crc32c/src',
]

THIRD_PARTY_TO_KEEP = [
  'src/third_party/BUILD.gn',
  'src/third_party/abseil-cpp',
  'src/third_party/google_benchmark',
  'src/third_party/googletest',
  'src/third_party/libsrtp',
  'src/third_party/libyuv',
  'src/third_party/nasm',
  'src/third_party/pffft',
  'src/third_party/rnnoise',
  'src/third_party/crc32c',
]

EXTRA_TO_REMOVE = [
  'src/examples',
]

MANIFEST = {}


def git_repository_sync_is_disabled(git, directory):
  try:
    disable = subprocess.check_output(
      [git, 'config', 'sync-deps.disable'], cwd=directory)
    return disable.lower().strip() in ['true', '1', 'yes', 'on']
  except subprocess.CalledProcessError:
    return False


def is_git_toplevel(git, directory):
  """Return true iff the directory is the top level of a Git repository.

  Args:
    git (string) the git executable

    directory (string) the path into which the repository
              is expected to be checked out.
  """
  try:
    toplevel = subprocess.check_output(
      [git, 'rev-parse', '--show-toplevel'], cwd=directory).strip()
    return (os.path.normcase(os.path.realpath(directory)) ==
            os.path.normcase(os.path.realpath(toplevel.decode())))
  except subprocess.CalledProcessError:
    return False


def status(directory, commithash, change):
  def truncate_beginning(s, length):
    return s if len(s) <= length else '...' + s[-(length-3):]
  def truncate_end(s, length):
    return s if len(s) <= length else s[:(length - 3)] + '...'

  dlen = 36
  directory = truncate_beginning(directory, dlen)
  commithash = truncate_end(commithash, 40)
  symbol = '>' if change else '@'
  sys.stdout.write('%-*s %s %s\n' % (dlen, directory, symbol, commithash))


def git_checkout_to_directory(git, repo, commithash, directory, shallow, verbose):
  """Checkout (and clone if needed) a Git repository.

  Args:
    git (string) the git executable

    repo (string) the location of the repository, suitable
         for passing to `git clone`.

    commithash (string) a commit, suitable for passing to `git checkout`

    directory (string) the path into which the repository
              should be checked out.

    verbose (boolean)

  Raises an exception if any calls to git fail.
  """

  MANIFEST[directory] = {
    "url": repo,
    "revision": commithash
  }

  if not os.path.isdir(directory):
    subprocess.check_call(
      [git, 'clone', '--quiet', *(['--depth=1'] if shallow else []),
       '--no-checkout', repo, directory])

  if not is_git_toplevel(git, directory):
    # if the directory exists, but isn't a git repo, you will modify
    # the parent repository, which isn't what you want.
    sys.stdout.write('%s\n  IS NOT TOP-LEVEL GIT DIRECTORY.\n' % directory)
    return

  # Check to see if this repo is disabled.  Quick return.
  if git_repository_sync_is_disabled(git, directory):
    sys.stdout.write('%s\n  SYNC IS DISABLED.\n' % directory)
    return

  with open(os.devnull, 'w') as devnull:
    # If this fails, we will fetch before trying again.  Don't spam user
    # with error infomation.
    if 0 == subprocess.call([git, 'checkout', '--quiet', commithash],
                            cwd=directory, stderr=devnull):
      # if this succeeds, skip slow `git fetch`.
      if verbose:
        status(directory, commithash, False)  # Success.
      return

  # If the repo has changed, always force use of the correct repo.
  # If origin already points to repo, this is a quick no-op.
  subprocess.check_call(
      [git, 'remote', 'set-url', 'origin', repo], cwd=directory)

  subprocess.check_call(
    [git, 'fetch', '--quiet',
     *(['--depth=1', repo, commithash] if shallow else [])],
    cwd=directory)

  subprocess.check_call([git, 'checkout', '--quiet', commithash], cwd=directory)

  if verbose:
    status(directory, commithash, True)  # Success.


def parse_file_to_dict(path):
  dictionary = {}
  helpers = (
    "def Var(x): return vars[x]\n"
    "def Str(x): return str(x)\n"
  )
  with open(path) as f:
    exec(helpers + f.read(), dictionary)
  return dictionary


def is_sha1_sum(s):
  """SHA1 sums are 160 bits, encoded as lowercase hexadecimal."""
  return len(s) == 40 and all(c in '0123456789abcdef' for c in s)


def git_sync_deps(git, deps_file_path, shallow, verbose):
  deps_file_directory = os.path.dirname(deps_file_path)
  deps_file = parse_file_to_dict(deps_file_path)
  dependencies = deps_file['deps'].copy()

  list_of_arg_lists = []
  for directory in sorted(dependencies):
    dep = dependencies[directory]
    dep_url = None
    if isinstance(dep, str):
      dep_url = dep
    elif isinstance(dep, dict) and 'url' in dep:
      if 'condition' in dep and dep['condition'] != 'checkout_linux':
        continue
      dep_url = dep['url']
    else:
      continue

    if '@' in dep_url:
      repo, commithash = dep_url.split('@', 1)
    else:
      raise Exception("please specify commit")
    if not is_sha1_sum(commithash):
      raise Exception("poorly formed commit hash: %r" % commithash)

    if not directory in DEPS_TO_USE:
      continue

    list_of_arg_lists.append(
      (git, repo, commithash, directory, shallow, verbose))

  for args in list_of_arg_lists:
    git_checkout_to_directory(*args)


def main(argv):
  parser = argparse.ArgumentParser()
  parser.add_argument(
      "--generate-tarball",
      action="store_true",
      help="Generate the final tarball after fetching source code"
  )
  parser.add_argument(
      "--revision",
      type=str,
      required=True,
      help="Specify the WebRTC revision to fetch"
  )
  args = parser.parse_args(argv)
  generate_tarball = args.generate_tarball

  src_dir='src'
  git='git'
  revision = args.revision
  sys.stdout.write('Cloning webrtc repository at revision %s ...\n' % revision)
  subprocess.check_call(
      [git, 'clone', '--quiet', '--depth=1', WEBRTC_URL, src_dir])
  subprocess.check_call([git, 'fetch', '--quiet', 'origin', 'refs/branch-heads/%s' % revision], cwd=src_dir)
  subprocess.check_call([git, 'checkout', '--quiet', 'FETCH_HEAD'], cwd=src_dir)

  bs = subprocess.check_output([git, 'show-ref', '-s', 'HEAD'], cwd=src_dir)
  gitrev = bs.decode('utf-8').rstrip('\n')
  MANIFEST['src'] = {
    "url": WEBRTC_URL,
    "revision": gitrev
  }

  git_sync_deps(git, os.path.join(src_dir, 'DEPS'), shallow=True, verbose=True)

  for entry in os.listdir('src/third_party/'):
    entry_path = os.path.join('src/third_party', entry)
    if not entry_path in THIRD_PARTY_TO_KEEP:
      sys.stdout.write('Removing %s ...\n' % entry_path)
      if os.path.isdir(entry_path):
        shutil.rmtree(entry_path)
      else:
        os.unlink(entry_path)

  for item in EXTRA_TO_REMOVE:
    if os.path.isdir(item):
      shutil.rmtree(item)

  if generate_tarball:
    bs = subprocess.check_output([git, 'show-ref', '-s', 'HEAD'], cwd=src_dir)
    gitrev = bs.decode('utf-8').rstrip('\n')
    timerev = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    version = "%s+%s" % (revision, timerev)
    target_dir = 'libanbox-webrtc-%s' % version

    sys.stdout.write('Creating archive ...\n')
    shutil.move(src_dir, target_dir)

    with open('%s/git-manifest' % target_dir, 'w') as f:
      f.write(json.dumps(MANIFEST, sort_keys=True, indent=2))

    subprocess.check_call(['tar', '--exclude-vcs', '-cJf', 'libanbox-webrtc-%s_%s.orig.tar.xz' % (revision, version), target_dir])

    shutil.rmtree(target_dir)

  return 0


if __name__ == '__main__':
  exit(main(sys.argv[1:]))
