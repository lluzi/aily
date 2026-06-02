import { Badge } from "@/components/ui/badge";
import { turnOnPlus } from "@/plusUtils";
import React, { useEffect } from "react";

export function PlusSettings() {
  useEffect(() => {
    turnOnPlus();
  }, []);

  return (
    <section className="tw-flex tw-flex-col tw-gap-4 tw-rounded-lg tw-bg-secondary tw-p-4">
      <div className="tw-flex tw-items-center tw-justify-between tw-gap-2 tw-text-xl tw-font-bold">
        <span>Chat</span>
        <Badge variant="outline" className="tw-text-success">
          Included
        </Badge>
      </div>
      <div className="tw-flex tw-flex-col tw-gap-2 tw-text-sm tw-text-muted">
        <div>
          Chat includes the Copilot Plus workflow surface by default. No upstream license key or
          separate purchase flow is required in this fork.
        </div>
        <div>
          Chat capabilities are routed through Aily&apos;s own backend, vault grounding, tool layer,
          and provider configuration:{" "}
          <strong>
            chat context, richer file context, web research, project workflows, autonomous tools,
            and grounded Aily synthesis are always available.
          </strong>
        </div>
      </div>
    </section>
  );
}
