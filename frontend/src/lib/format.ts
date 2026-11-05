const dateTimeFormat = new Intl.DateTimeFormat("en-IN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Kolkata",
});

const integerFormat = new Intl.NumberFormat("en-IN");

export const formatDateTime = (value: string) => dateTimeFormat.format(new Date(value));
export const formatCount = (value: number) => integerFormat.format(value);
