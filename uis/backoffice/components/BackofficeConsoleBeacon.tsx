"use client";

import { useEffect } from "react";

type BackofficeConsoleBeaconProps = {
  message: string;
};

export function BackofficeConsoleBeacon({ message }: BackofficeConsoleBeaconProps) {
  useEffect(() => {
    console.info(message);
  }, [message]);

  return null;
}
