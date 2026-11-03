{ pkgs }: {
  deps = [
    pkgs.ffmpeg
    pkgs.deno
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.bash
  ];
}
