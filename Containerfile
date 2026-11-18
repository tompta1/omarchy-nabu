FROM docker.io/library/alpine:3.22
RUN apk add --no-cache bash curl git python3 libarchive-tools e2fsprogs e2fsprogs-extra btrfs-progs dosfstools mtools util-linux coreutils gzip xz zstd shadow
WORKDIR /project
ENTRYPOINT ["/bin/bash"]
