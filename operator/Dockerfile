FROM golang:1.25 AS modules
WORKDIR /app
COPY go.mod go.sum* ./
RUN go mod download

FROM modules AS builder
COPY . .
RUN CGO_ENABLED=0 go build -o manager ./cmd/main.go

FROM gcr.io/distroless/static:nonroot
COPY --from=builder /app/manager /manager
ENTRYPOINT ["/manager"]
