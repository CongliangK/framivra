// Inject mouse events via uinput (absolute position + buttons).
// Usage:
//   uinject move <AX> <AY>    AX/AY in 0..32767 (absolute tablet coords)
//   uinject click <btn>       btn: 1=left 2=middle 3=right
// Requires write access to /dev/uinput (user in `input` group).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <linux/uinput.h>
#include <linux/input.h>

static int fd;

static void emit(int type, int code, int val) {
    struct input_event e = {0};
    e.type = type; e.code = code; e.value = val;
    if (write(fd, &e, sizeof(e)) < 0) perror("write");
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s move AX AY | click BTN\n", argv[0]); return 1; }

    fd = open("/dev/uinput", O_WRONLY);
    if (fd < 0) { perror("open /dev/uinput"); return 1; }
    ioctl(fd, UI_SET_EVBIT, EV_REL);
    ioctl(fd, UI_SET_RELBIT, REL_X);
    ioctl(fd, UI_SET_RELBIT, REL_Y);
    ioctl(fd, UI_SET_EVBIT, EV_KEY);
    ioctl(fd, UI_SET_EVBIT, EV_ABS);
    ioctl(fd, UI_SET_EVBIT, EV_SYN);
    ioctl(fd, UI_SET_KEYBIT, BTN_LEFT);
    ioctl(fd, UI_SET_KEYBIT, BTN_MIDDLE);
    ioctl(fd, UI_SET_KEYBIT, BTN_RIGHT);
    struct uinput_abs_setup as = {0};
    as.code = ABS_X; as.absinfo.minimum = 0; as.absinfo.maximum = 32767;
    ioctl(fd, UI_SET_ABSBIT, ABS_X);
    ioctl(fd, UI_ABS_SETUP, &as);
    as.code = ABS_Y;
    ioctl(fd, UI_SET_ABSBIT, ABS_Y);
    ioctl(fd, UI_ABS_SETUP, &as);

    struct uinput_setup us = {0};
    us.id.bustype = BUS_USB;
    us.id.vendor = 0x1234; us.id.product = 0x5679;
    strcpy(us.name, "uinject-mouse");
    if (ioctl(fd, UI_DEV_SETUP, &us) < 0) { perror("UI_DEV_SETUP"); return 1; }
    if (ioctl(fd, UI_DEV_CREATE) < 0) { perror("UI_DEV_CREATE"); return 1; }
    sleep(1); // let the compositor detect the new device

    if (strcmp(argv[1], "move") == 0 && argc == 4) {
        emit(EV_ABS, ABS_X, atoi(argv[2]));
        emit(EV_ABS, ABS_Y, atoi(argv[3]));
        emit(EV_SYN, SYN_REPORT, 0);
    } else if (strcmp(argv[1], "rel") == 0 && argc == 4) {
        emit(EV_REL, REL_X, atoi(argv[2]));
        emit(EV_REL, REL_Y, atoi(argv[3]));
        emit(EV_SYN, SYN_REPORT, 0);
    } else if (strcmp(argv[1], "esc") == 0 && argc == 2) {
        emit(EV_KEY, KEY_ESC, 1);
        emit(EV_SYN, SYN_REPORT, 0);
        usleep(120000);
        emit(EV_KEY, KEY_ESC, 0);
        emit(EV_SYN, SYN_REPORT, 0);
    } else if (strcmp(argv[1], "click") == 0 && argc == 3) {
        int btn = atoi(argv[2]);
        emit(EV_KEY, btn == 1 ? BTN_LEFT : btn == 2 ? BTN_MIDDLE : BTN_RIGHT, 1);
        emit(EV_SYN, SYN_REPORT, 0);
        usleep(120000);
        emit(EV_KEY, btn == 1 ? BTN_LEFT : btn == 2 ? BTN_MIDDLE : BTN_RIGHT, 0);
        emit(EV_SYN, SYN_REPORT, 0);
    } else { fprintf(stderr, "bad args\n"); return 1; }

    usleep(100000);
    ioctl(fd, UI_DEV_DESTROY);
    close(fd);
    return 0;
}
