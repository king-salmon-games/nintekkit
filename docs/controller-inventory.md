# Controller prerequisites

The alpha targets the existing Switchroot combined Joy-Con device, exposed as
`Nintendo Switch Combined Joy-Cons`, with four axes and eighteen buttons. It also
expects the matching XInput pointer and keyboard devices in the local X11 session.
Device numbers vary after reconnects and reboots; the mapper discovers them.

Use `scripts/controller/nintekkit_controller.py --inspect` through
`scripts/switch/desktop-run.py` to check the current device without changing input.
The mapper needs access to the joystick and X11/XInput/XTest/XRes libraries. Keep
the working Switchroot pairing and input drivers installed.

The contextual mapper temporarily detaches the two desktop XInput devices while
it owns game input, then restores their original attachments and flags. It uses
raw joystick events for movement and the client-tools companion for GUI context.
Unrecognized devices, stale context and lost game focus leave it inactive.

See [controls and recovery](controls.md) for the complete mapping and cleanup.
Other controllers, Wayland sessions and different desktop mapping stacks need
separate testing; this alpha does not claim compatibility with them.
