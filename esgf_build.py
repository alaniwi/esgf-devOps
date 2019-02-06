#!usr/bin/env python
"""Modules needed mostly to access terminal commands."""
import subprocess
import shlex
import os
import logging
import datetime
from git import Repo
import repo_info
import build_utilities
import semver
import click
from github_release import gh_release_create, gh_asset_upload, get_releases
from git import RemoteProgress
from plumbum.commands import ProcessExecutionError

logger = logging.basicConfig(level=logging.DEBUG,
                             format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("esgf_build")


###########################################
# Git Utility Functions

class ProgressPrinter(RemoteProgress):
    """Print the progress of cloning from GitHub on the command line."""

    def update(self, op_code, cur_count, max_count=None, message=''):
        """Print progress update message."""
        print op_code, cur_count, max_count, cur_count / (max_count or 100.0), message or "NO MESSAGE"


def get_latest_tag(repo):
    """Accept a GitPython Repo object and returns the latest annotated tag.

    Provides all the tags, reverses them (so that you can get the latest
    tag) and then takes only the first from the list.
    """
    # Fetch latest tags from GitHub
    build_utilities.call_binary("git", ["fetch", "--tags"])
    # A tag can point to a blob and the loop prunes blob tags from the list of tags to be sorted
    tag_list = []
    for bar in repo.tags:
        try:
            bar.commit.committed_datetime
        except ValueError:
            pass
        else:
            tag_list.append(bar)
    sorted_tags = sorted(tag_list, key=lambda t: t.commit.committed_datetime)
    latest_tag = str(sorted_tags[-1])
    return latest_tag


def create_taglist_file(taglist_file, repo_name, latest_tag):
    """Create a file containing the latest tag for each repo."""
    taglist_file.write("-------------------------\n")
    taglist_file.write(repo_name + "\n")
    taglist_file.write("-------------------------\n")
    taglist_file.write(latest_tag + "\n")
    taglist_file.write("\n")


def create_commits_since_last_tag_file(commits_since_last_tag_file, repo_name, latest_tag):
    """Create a file with a list of commits that have been pushed since the last tag was cut."""
    commits_since_tag = subprocess.check_output(shlex.split(
        "git log {latest_tag}..HEAD".format(latest_tag=latest_tag)))
    if commits_since_tag:
        print "There are new commits since the last annotated tag for {repo_name}".format(repo_name=repo_name)
        print "See commits_since_last_tag.txt for more details \n"
        commits_since_last_tag_file.write("-------------------------\n")
        commits_since_last_tag_file.write("Commits since last tag ({latest_tag}) for {repo_name}".format(
            latest_tag=latest_tag, repo_name=repo_name) + "\n")
        commits_since_last_tag_file.write("-------------------------\n")
        commits_since_last_tag_file.write(commits_since_tag + "\n")


def update_repo(repo_name, repo_object, active_branch):
    """Accept a GitPython Repo object and updates the specified branch."""
    if active_branch == "latest":
        active_tag = get_latest_tag(repo_object)
        print "Checkout {repo_name}'s {active_tag} tag".format(repo_name=repo_name, active_tag=active_tag)
        try:
            build_utilities.call_binary("git", ["checkout", active_tag, "-b", active_tag])
        except ProcessExecutionError, err:
            if err.retcode == 128:
                pass
    else:
        print "Checkout {repo_name}'s {active_branch} branch".format(repo_name=repo_name, active_branch=active_branch)
        repo_object.git.checkout(active_branch)

        progress_printer = ProgressPrinter()
        repo_object.remotes.origin.pull("{active_branch}:{active_branch}".format(
            active_branch=active_branch), progress=progress_printer)
    print "Updating: " + repo_name


def clone_repo(repo, repo_directory):
    """Clone a repository from GitHub."""
    repo_path = os.path.join(repo_directory, repo)
    print "Cloning {} repo from Github".format(repo)
    Repo.clone_from(repo_info.ALL_REPO_URLS[repo], repo_path,
                    progress=ProgressPrinter())
    print(repo + " successfully cloned -> {repo_path}".format(repo_path=repo_path))


def list_branches(repo_handle):
    """List all branches for a repo."""
    return [repo.name for repo in repo_handle.branches]


def update_all(branch, repo_directory, build_list):
    """Check each repo in the REPO_LIST for the most updated branch, and uses taglist to track versions."""
    print "Beginning to update directories."

    commits_since_last_tag_file = open(os.path.join(
        repo_directory, "commits_since_last_tag.txt"), "w")
    taglist_file = open(os.path.join(repo_directory, "taglist.txt"), "w+")

    for repo in build_list:
        try:
            os.chdir(repo_directory + "/" + repo)
        except OSError:
            print "Directory for {repo} does not exist".format(repo=repo)
            clone_repo(repo, repo_directory)
            os.chdir(repo_directory + "/" + repo)

        repo_handle = Repo(os.getcwd())

        if not branch:
            active_branch = choose_branch(repo_handle)
        elif branch == "latest":
            active_branch = "latest"
        elif branch not in list_branches(repo_handle):
            raise ValueError("{} branch was not found for {} repo".format(branch, repo))
        else:
            active_branch = branch

        print "Building {}".format(active_branch)
        # repo_branches = list_branches(repo_handle)
        # print "repo_branches:", repo_branches
        # import sys; sys.exit(0)
        update_repo(repo, repo_handle, active_branch)

        latest_tag = get_latest_tag(repo_handle)
        create_taglist_file(taglist_file, repo, latest_tag)

        create_commits_since_last_tag_file(commits_since_last_tag_file, repo, latest_tag)

        os.chdir("..")

    taglist_file.close()
    commits_since_last_tag_file.close()
    print "Directory updates complete."


def get_most_recent_commit(repo_handle):
    """Get the most recent commit w/ log and list comprehension."""
    repo_handle.git.log()
    mst_rcnt_cmmt = repo_handle.git.log().split("\ncommit")[0]
    return mst_rcnt_cmmt

###########################################
# Ant Utility Functions


def clean(repo, log_directory, clean_command="clean_all"):
    """Run the clean directive from a repo's build script."""
    clean_log = os.path.join(log_directory, repo + "-clean.log")
    with open(clean_log, "w") as clean_log_file:
        clean_output = build_utilities.call_binary("ant", [clean_command])
        clean_log_file.write(clean_output)


def pull(repo, log_directory, pull_command="pull"):
    """Run the pull directive from a repo's build script."""
    pull_log = log_directory + "/" + repo + "-pull.log"
    with open(pull_log, "w") as pull_log_file:
        pull_output = build_utilities.call_binary("ant", [pull_command])
        pull_log_file.write(pull_output)


def build(repo, log_directory, build_command="make_dist"):
    """Run the build directive from a repo's build script."""
    build_log = os.path.join(log_directory, repo + "-build.log")

    with open(build_log, "w") as build_log_file:
        build_output = build_utilities.call_binary("ant", [build_command])
        build_log_file.write(build_output)


def publish_local(repo, log_directory, publish_command="publish_local"):
    """Run the publish local directive from a repo's build script."""
    publish_local_log = log_directory + "/" + repo + "-publishlocal.log"
    with open(publish_local_log, "w") as publish_local_log_file:
        publish_local_output = build_utilities.call_binary("ant", [publish_command])
        publish_local_log_file.write(publish_local_output)


def build_all(build_list, starting_directory, bump):
    """Take a list of repositories to build, and uses ant to build them."""
    log_directory = starting_directory + "/buildlogs"
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)
    for repo in build_list:
        print "Building repo: " + repo
        os.chdir(starting_directory + "/" + repo)
        logger.info(os.getcwd())
        if bump:
            repo_handle = Repo(os.getcwd())
            latest_tag = get_latest_tag(repo_handle)
            new_tag = bump_tag_version(repo, latest_tag, bump)
            repo_handle.create_tag(new_tag, message='Updated {} version to tag "{}"'.format(bump, new_tag))

        # repos getcert and stats-api do not need an ant pull call
        if repo == 'esgf-getcert':
            # clean and dist only
            clean(repo, log_directory, clean_command="clean")
            build(repo, log_directory, build_command="dist")
            os.chdir("..")
            continue

        elif repo == 'esgf-stats-api':
            # clean and make_dist only
            clean(repo, log_directory)
            build(repo, log_directory)
            os.chdir('..')
            continue
        else:
            # clean, build, and make_dist, publish to local repo
            clean(repo, log_directory)
            pull(repo, log_directory)
            build(repo, log_directory)
            publish_local(repo, log_directory)
        os.chdir("..")

    print "\nRepository builds complete."
    create_build_history(build_list)


def create_build_history(build_list):
    """Create a directory to keep a history of the build logs."""
    # TODO: list clean, pull, and publish logs as well
    build_history_file = open("buildlogs/build_history_{}.log".format(datetime.date.today()), "a")
    build_history_file.write("Build Time: {}\n".format(str(datetime.datetime.now())))
    build_history_file.write("-----------------------------------------------------\n")
    for repo in build_list:
        build_log = 'buildlogs/{}-build.log'.format(repo)
        print "log_file:", build_log
        for line in reversed(open(build_log).readlines()):
            if "BUILD" in line:
                build_history_file.write("{}: {}".format(repo, line.rstrip()))
                build_history_file.write("\n")
                break
    build_history_file.close()


def bump_tag_version(repo, current_version, selection=None):
    """Bump the tag version using semantic versioning."""
    current_version = current_version.replace("v", "")
    while True:
        if not selection:
            print '----------------------------------------\n'
            print '0: Bump major version {} -> {} \n'.format(current_version, semver.bump_major(current_version))
            print '1: Bump minor version {} -> {} \n'.format(current_version, semver.bump_minor(current_version))
            print '2: Bump patch version {} -> {} \n'.format(current_version, semver.bump_patch(current_version))
            selection = raw_input("Choose version number component to increment: ")
        if selection == "0" or selection == "major":
            return "v" + semver.bump_major(current_version)
            break
        elif selection == "1" or selection == "minor":
            return "v" + semver.bump_minor(current_version)
            break
        elif selection == "2" or selection == "patch":
            return "v" + semver.bump_patch(current_version)
            break
        else:
            print "Invalid selection. Please make a valid selection."
            selection = None


def query_for_upload():
    """Choose whether or not to upload assets to GitHubself.

    Invokes when the upload command line option is not present.
    """
    while True:
        upload_assets = raw_input("Would you like to upload the built assets to GitHub? [Y/n]") or "yes"
        if upload_assets.lower() in ["y", "yes"]:
            upload = True
            break
        elif upload_assets.lower() in ["n", "no"]:
            upload = False
            break
        else:
            print "Please choose a valid option"
    return upload


def esgf_upload(starting_directory, build_list, name, upload_flag=False, prerelease_flag=False, dryrun=False):
    """Upload binaries to GitHub release as assets."""
    if upload_flag is None:
        upload_flag = query_for_upload()

    if not upload_flag:
        return

    if prerelease_flag:
        print "Marking as prerelease"

    print "build list in upload:", build_list
    for repo in build_list:
        print "repo:", repo
        os.chdir(os.path.join(starting_directory, repo))
        repo_handle = Repo(os.getcwd())
        latest_tag = get_latest_tag(repo_handle)
        print "latest_tag:", latest_tag

        if not name:
            release_name = latest_tag
        else:
            release_name = name

        if latest_tag in get_releases("ESGF/{}".format(repo)):
            print "Updating the assets for the latest tag {}".format(latest_tag)
            gh_asset_upload("ESGF/{}".format(repo), latest_tag, "{}/{}/dist/*".format(starting_directory, repo), dry_run=dryrun, verbose=False)
        else:
            print "Creating release version {} for {}".format(latest_tag, repo)
            gh_release_create("ESGF/{}".format(repo), "{}".format(latest_tag), publish=True, name=release_name, prerelease=prerelease_flag, dry_run=dryrun, asset_pattern="{}/{}/dist/*".format(starting_directory, repo))

    print "Upload completed!"


def create_build_list(select_repo, all_repos_opt):
    """Create a list of repos to build depending on a menu that the user picks from."""
    if all_repos_opt is True:
        build_list = repo_info.REPO_LIST
        print "Building repos: " + str(build_list)
        print "\n"
        return build_list

    # If the user has selcted the repos to build, the indexes are used to select
    # the repo names from the menu and they are appended to the build_list
    select_repo_list = select_repo.split(',')
    print "select_repo_list:", select_repo_list
    select_repo_map = map(int, select_repo_list)
    print "select_repo_map:", select_repo_map

    build_list = []
    for repo_num in select_repo_map:
        repo_name = repo_info.REPO_LIST[repo_num]
        build_list.append(repo_name)
    if not build_list:
        print "No applicable repos selected."
        exit()
    else:
        print "Building repos: " + str(build_list)
        print "\n"
        return build_list


def find_path_to_repos(starting_directory):
    """Check the path provided to the repos to see if it exists."""
    if os.path.isdir(os.path.realpath(starting_directory)):
        starting_directory = os.path.realpath(starting_directory)
        return True
    create_path_q = raw_input("The path does not exist. Do you want {} to be created? (Y or YES)".format(starting_directory)) or "y"
    if create_path_q.lower() not in ["yes", "y"]:
        print "Not a valid response. Directory not created."
        return False
    else:
        print "Creating directory {}".format(create_path_q)
        os.makedirs(starting_directory)
        starting_directory = os.path.realpath(starting_directory)
        return True



def choose_branch(repo_handle):
    """Choose a git branch or tag name to checkout and build."""
    branches = list_branches(repo_handle)
    while True:
        print "Available branches: ", branches
        active_branch = raw_input("Enter a branch name to checkout for the build. You can also enter 'latest' to build from the latest tag: ")

        if active_branch.lower() not in branches and active_branch.lower() not in ["latest"]:
            print "{} is not a valid branch.".format(active_branch)
            print "Please choose either a valid branch from the list or 'latest' for the most recent tag."
            continue
        else:
            break
    return active_branch


def choose_directory():
    """Choose the absolute path where the ESGF repos are located on your system.

    If the repos do not currently exist in the given directory, they will be cloned into the directory.
    """
    while True:
        starting_directory = raw_input("Please provide the path to the repositories on your system: ").strip()
        if find_path_to_repos(starting_directory):
            break
    return starting_directory


def select_repos():
    """Display a menu for a user to choose repos to be built.

    Use a raw_input statement to ask which repos should be built, then call
    the create_build_list with all_repos_opt set to either True or False
    """
    print repo_info.REPO_MENU
    while True:
        select_repo = raw_input("Which repositories will be built? (Hit [Enter] for all) ")
        if not select_repo:
            all_repo_q = raw_input("Do you want to build all repositories? (Y or YES) ")
            if all_repo_q.lower() not in ["yes", "y", ""]:
                print "Not a valid response."
                continue
            else:
                build_list = create_build_list(select_repo, all_repos_opt=True)
                break
        else:
            try:
                build_list = create_build_list(select_repo, all_repos_opt=False)
                break
            except (ValueError, IndexError), error:
                logger.error(error)
                print "Invalid entry, please enter repos to build."
                continue
    return build_list


def check_java_compiler():
    """Check if a suitable Java compiler is found.

    The ESGF webapps currently support being built with Java 8 (JRE class number 52).
    An exception will be raised if an incompatible Java compiler is found.
    """
    javac = build_utilities.call_binary("javac", ["-version"], stderr_output=True)
    javac = javac.split(" ")[1]
    if not javac.startswith("1.8.0"):
        raise EnvironmentError("Your Java compiler must be a Java 8 compiler (JRE class number 52). Java compiler version {} was found using javac -version".format(javac))


@click.command()
@click.option('--branch', '-b', default=None, help='Name of the git branch or tag to checkout and build')
@click.option('--bump', '--bumpversion', default=None, type=click.Choice(['major', 'minor', 'patch']), help='Bump the version number according to the Semantic Versioning specification')
@click.option('--directory', '-d', default=None, help="Directory where the ESGF repos are located on your system")
@click.option('--name', '-n', default=None, help="Name of the release")
@click.option('--upload/--no-upload', is_flag=True, default=None, help="Upload built assets to GitHub")
@click.option('--prerelease', '-p', is_flag=True, help="Tag release as prerelease")
@click.option('--dryrun', '-r', is_flag=True, help="Perform a dry run of the release")
@click.argument('repos', default=None, nargs=-1, type=click.Choice(['all', 'esgf-dashboard', 'esgf-getcert', 'esgf-idp', 'esgf-node-manager', 'esgf-security', 'esg-orp', 'esg-search', 'esgf-stats-api']))
def main(branch, directory, repos, upload, prerelease, dryrun, name, bump):
    """User prompted for build specifications and functions for build are called."""
    print "upload:", upload
    print "prerelease:", prerelease
    print "bump:", bump

    check_java_compiler()

    if not directory:
        starting_directory = choose_directory()
    else:
        if find_path_to_repos(directory):
            starting_directory = directory
        else:
            starting_directory = choose_directory()

    print "Using build directory {}".format(starting_directory)
    if repos:
        if "all" in repos:
            build_list = repo_info.REPO_LIST
        else:
            build_list = repos
    else:
        build_list = select_repos()

    print "build_list:", build_list


    update_all(branch, starting_directory, build_list)
    build_all(build_list, starting_directory, bump)
    esgf_upload(starting_directory, build_list, name, upload, prerelease, dryrun)


if __name__ == '__main__':
    main()
