"use client"

import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"
import { cn } from "cn"

function Dialog(props: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

/** A modal over a dimmed page: centred on larger screens, a sheet from the bottom edge on phones. */
function DialogContent({
  className,
  children,
  initialFocus,
  finalFocus,
  ...props
}: DialogPrimitive.Popup.Props) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Backdrop
        data-slot="dialog-backdrop"
        className="fixed inset-0 z-50 bg-foreground/30 transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0 motion-reduce:transition-none"
      />
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        initialFocus={initialFocus}
        finalFocus={finalFocus}
        className={cn(
          "fixed z-50 flex max-h-[min(44rem,calc(100dvh-1.5rem))] flex-col overflow-hidden border border-border bg-popover text-popover-foreground shadow-[0_1px_2px_rgb(0_0_0/0.04),0_12px_32px_-8px_rgb(0_0_0/0.18)] outline-none transition-[opacity,translate,scale] duration-150 ease-out data-ending-style:opacity-0 data-starting-style:opacity-0 motion-reduce:transition-none dark:shadow-[0_12px_32px_-8px_rgb(0_0_0/0.6)]",
          "max-sm:inset-x-0 max-sm:bottom-0 max-sm:rounded-t-2xl max-sm:data-ending-style:translate-y-4 max-sm:data-starting-style:translate-y-4",
          "sm:left-1/2 sm:top-1/2 sm:w-[min(34rem,calc(100vw-2rem))] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-2xl sm:data-ending-style:scale-[0.98] sm:data-starting-style:scale-[0.98]",
          className
        )}
        {...props}
      >
        {children}
      </DialogPrimitive.Popup>
    </DialogPrimitive.Portal>
  )
}

function DialogTitle({ className, ...props }: DialogPrimitive.Title.Props) {
  return <DialogPrimitive.Title data-slot="dialog-title" className={cn("text-[15px] font-semibold leading-6", className)} {...props} />
}

function DialogDescription({ className, ...props }: DialogPrimitive.Description.Props) {
  return <DialogPrimitive.Description data-slot="dialog-description" className={cn("text-[13px] leading-5 text-muted-foreground", className)} {...props} />
}

export { Dialog, DialogContent, DialogDescription, DialogTitle }
