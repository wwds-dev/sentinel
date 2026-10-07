/* macOS app executable for a lab app that runs live from its checkout.

   The interpreter runs *inside* this executable: it links the venv's base
   libpython and starts it in-process, with the venv's site-packages, instead
   of exec'ing `.venv/bin/python`. That is the whole point of the file. macOS
   names a process after the executable it is running, so a launcher that
   hands off to the venv's python — fork-and-exec or plain exec — gets
   "python" in Activity Monitor, the Dock, Cmd-Tab and Force Quit, and no
   amount of Info.plist or AppKit work in the app changes those afterwards.
   Running here, the process is the bundle's own and carries its name.

   Two modes, decided by the arguments:

   * App mode — no arguments (how Launch Services starts an app), or a first
     argument that is an app option such as `--selftest`. Runs
     `<checkout>/main.py` with any arguments passed through, from the checkout
     directory. When stderr is not a terminal, output goes to LOG_PATH.
   * Interpreter mode — anything else, e.g. `-u -m services.narrator.converter`.
     Behaves exactly like the venv's python. `sys.executable` points at this
     executable, so a worker the app starts with `sys.executable` comes back
     through here and is named after the app too.

   APP_NAME and LOG_PATH are set by the install script with -D, so this one
   source is shared, byte for byte, by every app that uses it. Keep the copies
   identical — `imprint/scripts/app_launcher.c` and
   `sentinel/scripts/app_launcher.c`.
*/

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef APP_NAME
#error "build with -DAPP_NAME=\"...\""
#endif
#ifndef LOG_PATH
#error "build with -DLOG_PATH=\"...\""
#endif

static void write_error(const char *message) {
    int fd = open(LOG_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        dprintf(fd, APP_NAME " cannot start: %s\n", message);
        close(fd);
    }
}

static int executable_path(char *output, size_t capacity) {
    char executable[PATH_MAX];
    uint32_t size = (uint32_t)sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) {
        return -1;
    }
    if (capacity < PATH_MAX || realpath(executable, output) == NULL) {
        return -1;
    }
    return 0;
}

static int read_project_root(const char *executable, char *output, size_t capacity) {
    /* <bundle>/Contents/MacOS/<exe>  ->  <bundle>/Contents/Resources/project_root.txt */
    char contents[PATH_MAX];
    if (snprintf(contents, sizeof(contents), "%s", executable) >= (int)sizeof(contents)) {
        return -1;
    }
    for (int i = 0; i < 2; ++i) {
        char *slash = strrchr(contents, '/');
        if (slash == NULL) {
            return -1;
        }
        *slash = '\0';
    }
    char config_path[PATH_MAX];
    if (snprintf(config_path, sizeof(config_path), "%s/Resources/project_root.txt",
                 contents) >= (int)sizeof(config_path)) {
        return -1;
    }

    FILE *config = fopen(config_path, "r");
    if (config == NULL) {
        return -1;
    }
    char *result = fgets(output, (int)capacity, config);
    fclose(config);
    if (result == NULL) {
        return -1;
    }
    output[strcspn(output, "\r\n")] = '\0';
    return output[0] == '\0' ? -1 : 0;
}

/* Python's own long options. Every other `--` argument belongs to the app. */
static int is_python_long_option(const char *arg) {
    static const char *const options[] = {
        "--version", "--help", "--help-env", "--help-xoptions", "--help-all",
        "--check-hash-based-pycs", NULL,
    };
    for (int i = 0; options[i] != NULL; ++i) {
        if (strcmp(arg, options[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

static int is_app_mode(int argc, char **argv) {
    if (argc < 2) {
        return 1;
    }
    const char *first = argv[1];
    return strncmp(first, "--", 2) == 0 && first[2] != '\0'
        && !is_python_long_option(first);
}

static void send_output_to_log(void) {
    if (isatty(STDERR_FILENO)) {
        return;
    }
    int log_fd = open(LOG_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (log_fd >= 0) {
        dup2(log_fd, STDOUT_FILENO);
        dup2(log_fd, STDERR_FILENO);
        close(log_fd);
    }
    int null_fd = open("/dev/null", O_RDONLY);
    if (null_fd >= 0) {
        dup2(null_fd, STDIN_FILENO);
        close(null_fd);
    }
}

static int fail(PyConfig *config, PyStatus status) {
    PyConfig_Clear(config);
    if (PyStatus_IsExit(status)) {
        return status.exitcode;
    }
    write_error(status.err_msg ? status.err_msg : "the interpreter did not start");
    Py_ExitStatusException(status);
    return 1;
}

int main(int argc, char **argv) {
    char self[PATH_MAX];
    char project_root[PATH_MAX];
    char python_bin[PATH_MAX];
    char main_py[PATH_MAX];

    if (executable_path(self, sizeof(self)) != 0
        || read_project_root(self, project_root, sizeof(project_root)) != 0) {
        write_error("the bundled project path is missing or unreadable");
        return 1;
    }
    if (snprintf(python_bin, sizeof(python_bin), "%s/.venv/bin/python", project_root)
            >= (int)sizeof(python_bin)
        || snprintf(main_py, sizeof(main_py), "%s/main.py", project_root)
            >= (int)sizeof(main_py)) {
        write_error("the project path is too long");
        return 1;
    }
    if (access(python_bin, X_OK) != 0 || access(main_py, R_OK) != 0) {
        write_error("the Lab checkout or its Python environment is missing");
        return 1;
    }

    int app_mode = is_app_mode(argc, argv);
    if (app_mode) {
        if (chdir(project_root) != 0) {
            write_error(strerror(errno));
            return 1;
        }
        send_output_to_log();
    }

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);

    /* Path configuration is computed as if the venv's python were running:
       Python finds `pyvenv.cfg` beside it, takes the stdlib from its `home`
       and the packages from the venv. sys.executable is corrected below. */
    status = PyConfig_SetBytesString(&config, &config.program_name, python_bin);
    if (PyStatus_Exception(status)) {
        return fail(&config, status);
    }
    status = PyConfig_SetBytesString(&config, &config.executable, python_bin);
    if (PyStatus_Exception(status)) {
        return fail(&config, status);
    }

    if (app_mode) {
        /* sys.argv is main.py followed by the app's own arguments. */
        config.parse_argv = 0;
        char **app_argv = calloc((size_t)argc + 1, sizeof(char *));
        if (app_argv == NULL) {
            write_error("out of memory");
            return 1;
        }
        app_argv[0] = main_py;
        for (int i = 1; i < argc; ++i) {
            app_argv[i] = argv[i];
        }
        status = PyConfig_SetBytesArgv(&config, argc, app_argv);
        free(app_argv);
        if (PyStatus_Exception(status)) {
            return fail(&config, status);
        }
        status = PyConfig_SetBytesString(&config, &config.run_filename, main_py);
        if (PyStatus_Exception(status)) {
            return fail(&config, status);
        }
    } else {
        status = PyConfig_SetBytesArgv(&config, argc, argv);
        if (PyStatus_Exception(status)) {
            return fail(&config, status);
        }
    }

    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) {
        return fail(&config, status);
    }
    PyConfig_Clear(&config);

    /* Children started with sys.executable come back through this binary,
       so they carry the app's name as well. */
    PyObject *executable = PyUnicode_DecodeFSDefault(self);
    if (executable == NULL || PySys_SetObject("executable", executable) != 0) {
        PyErr_Clear();
    }
    Py_XDECREF(executable);

    return Py_RunMain();
}
