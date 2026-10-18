"""Source checkout entry point for the packaged Linux service command."""

from memecoin_operator.linux_service import main

if __name__ == "__main__":
    raise SystemExit(main())
